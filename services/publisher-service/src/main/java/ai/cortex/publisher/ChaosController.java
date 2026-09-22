package ai.cortex.publisher;

import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.config.KafkaListenerEndpointRegistry;
import org.springframework.kafka.listener.MessageListenerContainer;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ChaosController {
    private static final Logger log = LoggerFactory.getLogger(ChaosController.class);
    private final ChaosState chaos;
    private final KafkaListenerEndpointRegistry registry;

    public ChaosController(ChaosState chaos, KafkaListenerEndpointRegistry registry) {
        this.chaos = chaos; this.registry = registry;
    }

    @GetMapping("/chaos")
    public Map<String, Object> state() { return chaos.snapshot(); }

    @PostMapping("/chaos/{name}")
    public Map<String, Object> toggle(@PathVariable String name, @RequestParam(defaultValue = "true") boolean on,
                                      @RequestParam(defaultValue = "800") long ms) {
        switch (name) {
            case "slow-render" -> chaos.slowRenderMs.set(on ? ms : 0);
            case "db-write-fail" -> chaos.dbWriteFail.set(on);
            case "heap-pressure" -> { chaos.heapPressure.set(on); if (!on) chaos.releaseHeap(); }
            case "consumer-pause" -> {
                MessageListenerContainer c = registry.getListenerContainer(ContentEventsConsumer.LISTENER_ID);
                if (c != null) { if (on) c.pause(); else c.resume(); }
                chaos.consumerPaused.set(on);
            }
            default -> throw new IllegalArgumentException("unknown chaos scenario: " + name);
        }
        log.warn("chaos_toggle scenario={} on={} state={}", name, on, chaos.snapshot());
        return chaos.snapshot();
    }
}
