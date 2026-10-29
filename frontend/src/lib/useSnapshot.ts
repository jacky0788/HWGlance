// 每秒抓一次快照；視窗被縮小或擋住（document.hidden）時完全不抓，回到前景馬上補一次。
import { useEffect, useState } from "react";
import type { Snapshot } from "../types";
import { api, errorMessage } from "./api";

const RETRY_MS = 3000;

export function useSnapshot(): { snap: Snapshot | null; error: string | null } {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer = 0;

    function schedule(ms: number) {
      window.clearTimeout(timer);
      timer = window.setTimeout(tick, ms);
    }

    async function tick() {
      if (document.hidden) return;
      try {
        const next = await api.snapshot();
        if (stopped) return;
        setSnap((prev) => (prev && prev.seq === next.seq && prev.state === next.state ? prev : next));
        setError(null);
        schedule(Math.max(250, (next.interval || 1) * 1000));
      } catch (e) {
        if (stopped) return;
        setError(errorMessage(e));
        schedule(RETRY_MS);
      }
    }

    function onVisibility() {
      if (!document.hidden) schedule(0);
    }

    document.addEventListener("visibilitychange", onVisibility);
    void tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return { snap, error };
}
