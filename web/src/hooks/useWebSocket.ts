import { useEffect, useRef, useState, useCallback } from "react";
import type { RunEvent } from "../types";

export function useRunEvents(runId: string | undefined, active: boolean) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const clear = useCallback(() => setEvents([]), []);

  useEffect(() => {
    if (!runId || !active) return;

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/api/v1/runs/${runId}/ws`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    ws.onmessage = (e) => {
      try {
        const event: RunEvent = JSON.parse(e.data);
        setEvents((prev) => [...prev, event]);
      } catch {
        // ignore non-JSON messages
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
      setConnected(false);
    };
  }, [runId, active]);

  return { events, connected, clear };
}
