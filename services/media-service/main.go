// cortex-media-service — Go: renders OpenGraph images for published content,
// caches them in Redis, and announces each render on Kafka (cortex.media.processed).
// Instrumented with the OpenTelemetry Go SDK: traces, metrics, runtime metrics.
package main

import (
	"context"
	goruntime "runtime"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/redis/go-redis/extra/redisotel/v9"
	"github.com/redis/go-redis/v9"
	"github.com/segmentio/kafka-go"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/contrib/instrumentation/runtime"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/propagation"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

const topicMediaProcessed = "cortex.media.processed"

// imageBytes is the size of the cached "PNG" (a realistic OG image is 20-60 KiB).
const imageBytes = 20 * 1024

type chaos struct {
	renderMs   atomic.Int64 // extra render time per miss
	ttlSeconds atomic.Int64 // cache TTL; 0 = no expiry (memory grows)
	leak       atomic.Bool  // leak one goroutine + 256 KiB per request
	leaked     atomic.Int64
}

type server struct {
	rdb    *redis.Client
	writer *kafka.Writer
	tracer trace.Tracer
	log    *slog.Logger
	chaos  chaos
	base   int64 // base render ms

	reqCounter  metric.Int64Counter
	renderHist  metric.Float64Histogram
	leakedGauge metric.Int64UpDownCounter
	block       chan struct{}
	mu          sync.Mutex
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int64) int64 {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			return n
		}
	}
	return def
}

func setupOtel(ctx context.Context) (func(context.Context) error, error) {
	res, err := resource.New(ctx, resource.WithFromEnv(), resource.WithTelemetrySDK(), resource.WithHost())
	if err != nil {
		return nil, err
	}
	texp, err := otlptracegrpc.New(ctx)
	if err != nil {
		return nil, err
	}
	tp := sdktrace.NewTracerProvider(sdktrace.WithBatcher(texp), sdktrace.WithResource(res))
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))

	mexp, err := otlpmetricgrpc.New(ctx)
	if err != nil {
		return nil, err
	}
	mp := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(mexp, sdkmetric.WithInterval(15*time.Second))),
		sdkmetric.WithResource(res))
	otel.SetMeterProvider(mp)
	if err := runtime.Start(runtime.WithMinimumReadMemStatsInterval(10 * time.Second)); err != nil {
		return nil, err
	}
	return func(ctx context.Context) error {
		_ = tp.Shutdown(ctx)
		return mp.Shutdown(ctx)
	}, nil
}

func (s *server) logCtx(ctx context.Context) *slog.Logger {
	sc := trace.SpanContextFromContext(ctx)
	if !sc.IsValid() {
		return s.log
	}
	return s.log.With("trace_id", sc.TraceID().String(), "span_id", sc.SpanID().String())
}

type ogRequest struct {
	ContentID string `json:"content_id"`
	Title     string `json:"title"`
	Variant   string `json:"variant"` // delivery channel: web, amp, rss, ... (one image per variant)
	Force     bool   `json:"force"`
}

// render burns CPU for the configured time and allocates like an image encoder would.
func (s *server) render(ms int64) []byte {
	buf := make([]byte, 200*1024)
	deadline := time.Now().Add(time.Duration(ms) * time.Millisecond)
	x := uint64(1)
	for time.Now().Before(deadline) {
		for i := 0; i < 4096; i++ {
			x = x*6364136223846793005 + 1442695040888963407
			buf[i%len(buf)] = byte(x >> 56)
		}
	}
	return buf
}

func (s *server) handleOgImage(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var req ogRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.ContentID == "" {
		http.Error(w, `{"error":"content_id required"}`, http.StatusBadRequest)
		return
	}
	span := trace.SpanFromContext(ctx)
	if req.Variant == "" {
		req.Variant = "web"
	}
	span.SetAttributes(attribute.String("content.id", req.ContentID), attribute.String("media.variant", req.Variant), attribute.Bool("media.force", req.Force))

	if s.chaos.leak.Load() {
		go func() { buf := make([]byte, 256*1024); _ = buf; <-s.block }()
		s.chaos.leaked.Add(1)
		s.leakedGauge.Add(ctx, 1)
	}

	key := "media:og:" + req.ContentID + ":" + req.Variant
	url := fmt.Sprintf("https://cdn.cortex.local/og/%s-%s.png", req.ContentID, req.Variant)
	if !req.Force {
		if n, err := s.rdb.StrLen(ctx, key).Result(); err == nil && n > 0 {
			s.reqCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("result", "hit")))
			span.SetAttributes(attribute.String("media.cache", "hit"))
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"url": url, "cache": "hit", "render_ms": 0, "bytes": n})
			return
		}
	}

	// cache miss → render
	ms := s.base + s.chaos.renderMs.Load()
	_, rspan := s.tracer.Start(ctx, "media.render", trace.WithAttributes(attribute.Int64("media.render.budget_ms", ms)))
	t0 := time.Now()
	img := s.render(ms)
	took := time.Since(t0)
	rspan.End()
	s.renderHist.Record(ctx, took.Seconds())

	ttl := time.Duration(s.chaos.ttlSeconds.Load()) * time.Second // 0 → no expiry
	if err := s.rdb.Set(ctx, key, img[:imageBytes], ttl).Err(); err != nil {
		s.logCtx(ctx).Error("redis set failed", "key", key, "err", err.Error())
		s.reqCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("result", "error")))
		http.Error(w, `{"error":"cache write failed"}`, http.StatusBadGateway)
		return
	}
	s.reqCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("result", "miss")))
	span.SetAttributes(attribute.String("media.cache", "miss"))
	s.publishProcessed(ctx, req.ContentID, url, took)

	s.logCtx(ctx).Info("og image rendered", "content_id", req.ContentID, "variant", req.Variant, "render_ms", took.Milliseconds(), "ttl_s", int64(ttl.Seconds()), "force", req.Force)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"url": url, "cache": "miss", "render_ms": took.Milliseconds(), "bytes": imageBytes})
}

func (s *server) publishProcessed(ctx context.Context, id, url string, took time.Duration) {
	ctx, span := s.tracer.Start(ctx, topicMediaProcessed+" publish", trace.WithSpanKind(trace.SpanKindProducer),
		trace.WithAttributes(attribute.String("messaging.system", "kafka"), attribute.String("messaging.destination.name", topicMediaProcessed)))
	defer span.End()
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	headers := make([]kafka.Header, 0, len(carrier))
	for k, v := range carrier {
		headers = append(headers, kafka.Header{Key: k, Value: []byte(v)})
	}
	payload, _ := json.Marshal(map[string]any{"content_id": id, "url": url, "render_ms": took.Milliseconds(), "ts": time.Now().UTC().Format(time.RFC3339)})
	if err := s.writer.WriteMessages(ctx, kafka.Message{Key: []byte(id), Value: payload, Headers: headers}); err != nil {
		span.RecordError(err)
		s.logCtx(ctx).Warn("kafka publish failed", "err", err.Error())
	}
}

func (s *server) handleChaos(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodPost {
		scenario := r.URL.Query().Get("scenario")
		on := r.URL.Query().Get("on") != "false"
		switch scenario {
		case "media-slow-render":
			if on {
				s.chaos.renderMs.Store(envInt("CHAOS_SLOW_RENDER_MS", 600))
			} else {
				s.chaos.renderMs.Store(0)
			}
		case "cache-stampede":
			if on {
				s.chaos.ttlSeconds.Store(1)
			} else {
				s.chaos.ttlSeconds.Store(envInt("CACHE_TTL_SECONDS", 3600))
			}
		case "no-ttl":
			if on {
				s.chaos.ttlSeconds.Store(0)
			} else {
				s.chaos.ttlSeconds.Store(envInt("CACHE_TTL_SECONDS", 3600))
			}
		case "goroutine-leak":
			s.chaos.leak.Store(on)
			if !on {
				s.mu.Lock()
				close(s.block)
				s.block = make(chan struct{})
				s.mu.Unlock()
				s.leakedGauge.Add(r.Context(), -s.chaos.leaked.Swap(0))
			}
		default:
			http.Error(w, `{"error":"unknown scenario"}`, http.StatusBadRequest)
			return
		}
		s.log.Warn("chaos_toggle", "scenario", scenario, "on", on)
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"media-slow-render": s.chaos.renderMs.Load() > 0,
		"cache-stampede":    s.chaos.ttlSeconds.Load() == 1,
		"no-ttl":            s.chaos.ttlSeconds.Load() == 0,
		"goroutine-leak":    s.chaos.leak.Load(),
		"leaked_goroutines": s.chaos.leaked.Load(),
		"ttl_seconds":       s.chaos.ttlSeconds.Load(),
		"render_ms":         s.base + s.chaos.renderMs.Load(),
	})
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service_name", env("OTEL_SERVICE_NAME", "cortex-media-service"))

	shutdown, err := setupOtel(ctx)
	if err != nil {
		logger.Error("otel setup failed", "err", err.Error())
		os.Exit(1)
	}
	defer func() { _ = shutdown(context.Background()) }()

	rdb := redis.NewClient(&redis.Options{Addr: env("CACHE_REDIS_ADDR", "cache-redis:6379"), PoolSize: int(envInt("REDIS_POOL_SIZE", 16))})
	if err := redisotel.InstrumentTracing(rdb); err != nil {
		logger.Error("redisotel tracing", "err", err.Error())
	}
	if err := redisotel.InstrumentMetrics(rdb); err != nil {
		logger.Error("redisotel metrics", "err", err.Error())
	}

	writer := &kafka.Writer{
		Addr:                   kafka.TCP(strings.Split(env("KAFKA_BOOTSTRAP", "kafka:9092"), ",")...),
		Topic:                  topicMediaProcessed,
		Balancer:               &kafka.LeastBytes{},
		Async:                  true,
		AllowAutoTopicCreation: true,
		RequiredAcks:           kafka.RequireOne,
		BatchTimeout:           50 * time.Millisecond,
	}
	defer writer.Close()

	meter := otel.Meter("cortex-media-service")
	reqCounter, _ := meter.Int64Counter("media.requests", metric.WithDescription("OG image requests by cache result"))
	renderHist, _ := meter.Float64Histogram("media.render.duration", metric.WithUnit("s"), metric.WithDescription("Time spent rendering an OG image on a cache miss"),
		metric.WithExplicitBucketBoundaries(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
	leakedGauge, _ := meter.Int64UpDownCounter("media.leaked.goroutines", metric.WithDescription("Goroutines deliberately leaked by the chaos toggle"))
	_, _ = meter.Int64ObservableGauge("media.goroutines", metric.WithDescription("Live goroutines in media-service"),
		metric.WithInt64Callback(func(_ context.Context, o metric.Int64Observer) error { o.Observe(int64(goruntime.NumGoroutine())); return nil }))

	s := &server{rdb: rdb, writer: writer, tracer: otel.Tracer("cortex-media-service"), log: logger, base: envInt("RENDER_MS", 40),
		reqCounter: reqCounter, renderHist: renderHist, leakedGauge: leakedGauge, block: make(chan struct{})}
	s.chaos.ttlSeconds.Store(envInt("CACHE_TTL_SECONDS", 3600))

	mux := http.NewServeMux()
	mux.Handle("POST /media/og-image", otelhttp.WithRouteTag("/media/og-image", http.HandlerFunc(s.handleOgImage)))
	mux.HandleFunc("/chaos", s.handleChaos)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		if err := rdb.Ping(r.Context()).Err(); err != nil {
			http.Error(w, "redis: "+err.Error(), http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte("ok"))
	})
	handler := otelhttp.NewHandler(mux, "media-service", otelhttp.WithFilter(func(r *http.Request) bool { return r.URL.Path != "/healthz" }))

	addr := ":" + env("PORT", "8086")
	srv := &http.Server{Addr: addr, Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		logger.Info("media-service listening", "addr", addr, "render_ms", s.base, "ttl_s", s.chaos.ttlSeconds.Load())
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("server failed", "err", err.Error())
			os.Exit(1)
		}
	}()
	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
}
