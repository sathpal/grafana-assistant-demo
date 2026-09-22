package ai.cortex.publisher;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class ContentEventsConsumer {
    public static final String LISTENER_ID = "content-events";
    private static final Logger log = LoggerFactory.getLogger(ContentEventsConsumer.class);
    private final PublishService service;
    private final ObjectMapper om;

    public ContentEventsConsumer(PublishService service, ObjectMapper om) {
        this.service = service; this.om = om;
    }

    @KafkaListener(id = LISTENER_ID, topics = {Topics.CONTENT_APPROVED, Topics.CONTENT_REINDEX}, groupId = "publisher-service")
    public void onMessage(ConsumerRecord<String, String> rec) throws Exception {
        ContentEvent ev = om.readValue(rec.value(), ContentEvent.class);
        String source = rec.topic().equals(Topics.CONTENT_APPROVED) ? "approved" : "reindex";
        log.info("consume topic={} partition={} offset={} content_id={} run_id={}", rec.topic(), rec.partition(), rec.offset(), ev.contentId(), ev.runId());
        service.publish(ev, source);
    }
}
