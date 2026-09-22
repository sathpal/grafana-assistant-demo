package ai.cortex.publisher;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class PublishedController {
    private final PublishedContentRepository repo;
    private final StringRedisTemplate redis;
    private final PublishService service;
    private final ObjectMapper om;

    public PublishedController(PublishedContentRepository repo, StringRedisTemplate redis, PublishService service, ObjectMapper om) {
        this.repo = repo; this.redis = redis; this.service = service; this.om = om;
    }

    /** The "site" read path: Redis first, Postgres on a miss. */
    @GetMapping("/published/{id}")
    public ResponseEntity<?> get(@PathVariable String id) throws Exception {
        String cached = redis.opsForValue().get("published:" + id);
        if (cached != null) {
            Map<?, ?> m = om.readValue(cached, Map.class);
            Map<String, Object> out = new LinkedHashMap<>();
            m.forEach((k, v) -> out.put(String.valueOf(k), v));
            out.put("cache", "hit");
            return ResponseEntity.ok(out);
        }
        return repo.findById(id).map(row -> {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("content_id", row.getContentId());
            out.put("title", row.getTitle());
            out.put("html", row.getHtml());
            out.put("media_url", row.getMediaUrl());
            out.put("version", row.getVersion());
            out.put("published_at", row.getPublishedAt() == null ? null : row.getPublishedAt().toString());
            out.put("cache", "miss");
            try {
                redis.opsForValue().set("published:" + id, om.writeValueAsString(out), Duration.ofHours(1));
            } catch (Exception ignored) { }
            return ResponseEntity.ok((Object) out);
        }).orElseGet(() -> ResponseEntity.notFound().build());
    }

    @GetMapping("/published/ids")
    public List<String> ids(@RequestParam(defaultValue = "500") int limit) {
        return repo.findIds(Math.min(limit, 5000));
    }

    /** Direct publish (seeding, tests). Same path as the Kafka consumer. */
    @PostMapping("/publish")
    public Map<String, Object> publish(@RequestBody ContentEvent ev) {
        return service.publish(ev, ev.source() == null ? "direct" : ev.source());
    }
}
