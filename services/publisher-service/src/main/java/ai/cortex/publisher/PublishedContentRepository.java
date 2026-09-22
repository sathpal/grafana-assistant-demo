package ai.cortex.publisher;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface PublishedContentRepository extends JpaRepository<PublishedContent, String> {
    @Query(value = "select content_id from published_content order by content_id limit :limit", nativeQuery = true)
    List<String> findIds(int limit);
}
