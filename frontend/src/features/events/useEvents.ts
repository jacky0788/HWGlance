// 事件記錄的資料。讀事件記錄要零點幾秒到一秒（後端快取 60 秒），所以不像快照每秒抓：
// 事件頁開著時每 2 分鐘、頂端列的徽章每 5 分鐘抓一次；視窗看不到時不抓，回到前景再補。
import { useEffect, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import type { EventCategory, EventReport } from "../../types";

export function useEventReport(days: number, everyMs: number): {
  report: EventReport | null; // 換範圍時先留著上一份，畫面不會閃
  error: string | null;
  loading: boolean;
} {
  const [report, setReport] = useState<EventReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let stopped = false;
    let busy = false;
    let timer = 0;
    let last = 0;

    function schedule(ms: number) {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void load(), ms);
    }

    async function load() {
      if (document.hidden || busy) return; // 回到前景時 onVisibility 會補；抓到一半的結束後會自己排下一次
      busy = true;
      setLoading(true);
      try {
        const next = await api.events(days);
        if (stopped) return;
        setReport(next);
        setError(null);
      } catch (e) {
        if (!stopped) setError(errorMessage(e));
      } finally {
        busy = false;
        if (!stopped) {
          setLoading(false);
          last = Date.now();
          schedule(everyMs);
        }
      }
    }

    function onVisibility() {
      if (!document.hidden) schedule(Math.max(0, last + everyMs - Date.now()));
    }

    document.addEventListener("visibilitychange", onVisibility);
    void load();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [days, everyMs]);

  return { report, error, loading };
}

export interface EventBadge {
  count: number;
  level: "hot" | "warm";
  title: string;
}

const BADGE_PARTS: [EventCategory, string][] = [["crash", "當機"], ["gpu", "顯示卡"], ["storage", "硬碟"], ["hardware", "CPU／記憶體"]];

// 頂端列「事件記錄」旁的數字：最近 7 天要特別注意的事（當機、硬碟斷線、顯示卡致命錯誤…），沒有就不顯示
export function useEventBadge(): EventBadge | null {
  const { report } = useEventReport(7, 5 * 60_000);
  const major = report?.incidents.filter((i) => i.major) ?? [];
  if (major.length === 0) return null;
  const parts = BADGE_PARTS.map(([cat, label]) => [label, major.filter((i) => i.category === cat).length] as const)
    .filter(([, n]) => n > 0)
    .map(([label, n]) => `${label} ${n}`);
  return {
    count: major.length,
    level: major.some((i) => i.level === "hot") ? "hot" : "warm",
    title: `最近 7 天有 ${major.length} 件要注意的事${parts.length ? `：${parts.join("、")}` : ""}`,
  };
}
