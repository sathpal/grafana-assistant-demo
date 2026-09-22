package ai.cortex.publisher;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "published_content")
public class PublishedContent {
    @Id
    @Column(name = "content_id", length = 128)
    private String contentId;
    @Column(length = 512)
    private String title;
    @Column(columnDefinition = "text")
    private String html;
    @Column(length = 64)
    private String kind;
    @Column(length = 64)
    private String source;
    @Column(name = "media_url", length = 512)
    private String mediaUrl;
    @Column(name = "published_at")
    private Instant publishedAt;
    private int version;

    public String getContentId() { return contentId; }
    public void setContentId(String v) { contentId = v; }
    public String getTitle() { return title; }
    public void setTitle(String v) { title = v; }
    public String getHtml() { return html; }
    public void setHtml(String v) { html = v; }
    public String getKind() { return kind; }
    public void setKind(String v) { kind = v; }
    public String getSource() { return source; }
    public void setSource(String v) { source = v; }
    public String getMediaUrl() { return mediaUrl; }
    public void setMediaUrl(String v) { mediaUrl = v; }
    public Instant getPublishedAt() { return publishedAt; }
    public void setPublishedAt(Instant v) { publishedAt = v; }
    public int getVersion() { return version; }
    public void setVersion(int v) { version = v; }
}
