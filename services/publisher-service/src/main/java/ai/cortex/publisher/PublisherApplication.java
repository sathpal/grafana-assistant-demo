package ai.cortex.publisher;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.kafka.config.TopicBuilder;

@SpringBootApplication
public class PublisherApplication {
    public static void main(String[] args) {
        SpringApplication.run(PublisherApplication.class, args);
    }

    @Bean NewTopic approvedTopic() { return TopicBuilder.name(Topics.CONTENT_APPROVED).partitions(3).replicas(1).build(); }
    @Bean NewTopic reindexTopic()  { return TopicBuilder.name(Topics.CONTENT_REINDEX).partitions(3).replicas(1).build(); }
    @Bean NewTopic mediaTopic()    { return TopicBuilder.name(Topics.MEDIA_PROCESSED).partitions(3).replicas(1).build(); }
}
