package ai.cortex.publisher;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/** Runtime failure toggles for the demo. Every toggle is logged so it shows up in Loki. */
@Component
public class ChaosState {
    private static final Logger log = LoggerFactory.getLogger(ChaosState.class);
    public final AtomicLong slowRenderMs = new AtomicLong(0);
    public final AtomicBoolean dbWriteFail = new AtomicBoolean(false);
    public final AtomicBoolean heapPressure = new AtomicBoolean(false);
    public final AtomicBoolean consumerPaused = new AtomicBoolean(false);
    private final List<byte[]> retained = new ArrayList<>();

    public synchronized void retainHeapIfEnabled() {
        if (heapPressure.get() && retained.size() < 120) {
            retained.add(new byte[1024 * 1024]);          // +1 MiB per publish, capped at 120 MiB
            log.warn("chaos heap-pressure retained_mib={}", retained.size());
        }
    }

    public synchronized void releaseHeap() { retained.clear(); }

    public void slowRenderIfEnabled() {
        long ms = slowRenderMs.get();
        if (ms > 0) {
            try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
    }

    public boolean isDbWriteFail() { return dbWriteFail.get(); }

    public Map<String, Object> snapshot() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("slow-render", slowRenderMs.get() > 0 ? slowRenderMs.get() + "ms" : "off");
        m.put("db-write-fail", dbWriteFail.get());
        m.put("heap-pressure", heapPressure.get());
        m.put("consumer-pause", consumerPaused.get());
        m.put("retained_mib", retained.size());
        return m;
    }
}
