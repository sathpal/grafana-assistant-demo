package ai.cortex.publisher;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/** One piece of content moving through the publishing tier (Kafka JSON payload). */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ContentEvent(
        @JsonProperty("content_id") String contentId,
        @JsonProperty("title") String title,
        @JsonProperty("body_md") String bodyMd,
        @JsonProperty("kind") String kind,
        @JsonProperty("source") String source,
        @JsonProperty("run_id") String runId,
        @JsonProperty("force_media") Boolean forceMedia) {
    public boolean force() { return forceMedia != null && forceMedia; }
}
