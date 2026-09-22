package ai.cortex.publisher;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleHistogram;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.StatusCode;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.commonmark.node.Node;
import org.commonmark.parser.Parser;
import org.commonmark.renderer.html.HtmlRenderer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

/**
 * The publish path: render markdown → fetch OG image from media-service → persist → cache.
 * Every step is a child span of the Kafka consumer span (OTel Java agent), so one trace
 * covers Python producer → Kafka → this JVM → Go media-service → Redis → Postgres.
 */
@Service
public class PublishService {
    private static final Logger log = LoggerFactory.getLogger(PublishService.class);
    private final PublishedContentRepository repo;
    private final StringRedisTemplate redis;
    private final MediaClient media;
    private final ChaosState chaos;
    private final MeterRegistry meters;
    private final ObjectMapper om;
    private final Parser parser = Parser.builder().build();
    private final HtmlRenderer renderer = HtmlRenderer.builder().build();
    private final long baseRenderMs;
    // OTel API histogram (ms, default explicit buckets 0..10000 ms) so p95 works in Mimir.
    private final DoubleHistogram publishDuration = GlobalOpenTelemetry.getMeter("cortex-publisher-service")
            .histogramBuilder("publisher.publish.duration").setUnit("ms")
            .setDescription("End-to-end publish duration per content item").build();

    public PublishService(PublishedContentRepository repo, StringRedisTemplate redis, MediaClient media,
                          ChaosState chaos, MeterRegistry meters, ObjectMapper om,
                          @Value("${publisher.render-ms}") long baseRenderMs) {
        this.repo = repo; this.redis = redis; this.media = media; this.chaos = chaos;
        this.meters = meters; this.om = om; this.baseRenderMs = baseRenderMs;
    }

    public Map<String, Object> publish(ContentEvent ev, String source) {
        long t0 = System.nanoTime();
        Span span = Span.current();
        span.setAttribute("content.id", ev.contentId());
        span.setAttribute("content.source", source);
        span.setAttribute("content.kind", ev.kind() == null ? "blog" : ev.kind());
        String outcome = "ok";
        Map<String, Object> result = new LinkedHashMap<>();
        try {
            Node doc = parser.parse(ev.bodyMd() == null ? "" : ev.bodyMd());
            String html = renderer.render(doc);
            sleep(baseRenderMs);
            chaos.slowRenderIfEnabled();
            chaos.retainHeapIfEnabled();

            MediaClient.MediaResult mr = media.ogImage(ev.contentId(), ev.title(), ev.kind() == null ? "web" : ev.kind(), ev.force());
            span.setAttribute("media.cache", mr.cache());

            if (chaos.isDbWriteFail()) {
                throw new DbWriteException("published_content write rejected (chaos db-write-fail)");
            }
            PublishedContent row = repo.findById(ev.contentId()).orElseGet(PublishedContent::new);
            row.setContentId(ev.contentId());
            row.setTitle(ev.title());
            row.setHtml(html);
            row.setKind(ev.kind() == null ? "blog" : ev.kind());
            row.setSource(source);
            row.setMediaUrl(mr.url());
            row.setPublishedAt(Instant.now());
            row.setVersion(row.getVersion() + 1);
            repo.save(row);

            Map<String, Object> cached = new LinkedHashMap<>();
            cached.put("content_id", row.getContentId());
            cached.put("title", row.getTitle());
            cached.put("html", row.getHtml());
            cached.put("media_url", row.getMediaUrl());
            cached.put("version", row.getVersion());
            cached.put("published_at", row.getPublishedAt().toString());
            redis.opsForValue().set("published:" + row.getContentId(), om.writeValueAsString(cached), Duration.ofHours(1));

            meters.counter("publisher.content.published", "source", source).increment();
            log.info("published content_id={} source={} version={} media_cache={}", row.getContentId(), source, row.getVersion(), mr.cache());
            result.put("status", "published");
            result.put("content_id", row.getContentId());
            result.put("version", row.getVersion());
            result.put("media_cache", mr.cache());
            return result;
        } catch (Exception e) {
            String reason = (e instanceof DbWriteException) ? "db_write"
                    : (e instanceof MediaClient.MediaException) ? "media" : "unexpected";
            outcome = reason;
            meters.counter("publisher.publish.failures", "reason", reason, "source", source).increment();
            span.recordException(e);
            span.setStatus(StatusCode.ERROR, reason);
            log.error("publish_failed content_id={} source={} reason={} error={}", ev.contentId(), source, reason, e.getMessage());
            result.put("status", "failed");
            result.put("content_id", ev.contentId());
            result.put("reason", reason);
            return result;
        } finally {
            publishDuration.record((System.nanoTime() - t0) / 1_000_000.0,
                    Attributes.builder().put("source", source).put("outcome", outcome).build());
        }
    }

    private static void sleep(long ms) {
        if (ms <= 0) return;
        try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }

    static class DbWriteException extends RuntimeException {
        DbWriteException(String msg) { super(msg); }
    }
}
