package ai.cortex.publisher;

import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** Calls the Go media-service to produce the OpenGraph image for a piece of content. */
@Component
public class MediaClient {
    public record MediaResult(String url, String cache, long render_ms) {}
    public static class MediaException extends RuntimeException {
        public MediaException(String msg, Throwable cause) { super(msg, cause); }
    }

    private final RestClient client;

    public MediaClient(@Value("${publisher.media-base-url}") String baseUrl) {
        this.client = RestClient.builder().baseUrl(baseUrl).build();
    }

    public MediaResult ogImage(String contentId, String title, String variant, boolean force) {
        try {
            return client.post().uri("/media/og-image")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("content_id", contentId, "title", title == null ? "" : title, "variant", variant == null ? "web" : variant, "force", force))
                    .retrieve()
                    .body(MediaResult.class);
        } catch (Exception e) {
            throw new MediaException("media-service call failed for " + contentId + ": " + e.getMessage(), e);
        }
    }
}
