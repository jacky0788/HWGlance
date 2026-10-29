// 折線圖的資料：打開時先拿後端記住的最近幾分鐘，之後每份新快照接一格上去。
// 鍵跟後端 normalize.history_values 一樣：溫度的 id，或「硬體 id#load／#power」。
import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "../types";
import { api } from "./api";

export type Series = Record<string, (number | null)[]>;

export const HISTORY_SECONDS = 300;

function valuesOf(snap: Snapshot): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const d of snap.devices) {
    for (const t of d.temps) out[t.id] = t.value;
    for (const s of d.stats) if (s.key === "load" || s.key === "power") out[`${d.id}#${s.key}`] = s.value;
  }
  return out;
}

export function useHistory(snap: Snapshot | null, seconds = HISTORY_SECONDS): Series {
  const [series, setSeries] = useState<Series>({});
  const lastTime = useRef(0);

  useEffect(() => {
    let cancelled = false;
    api
      .history(seconds)
      .then((h) => {
        if (cancelled || h.t.length === 0) return;
        lastTime.current = Math.max(lastTime.current, h.t[h.t.length - 1]);
        setSeries(h.series);
      })
      .catch(() => {
        // 拿不到歷史就從現在開始畫
      });
    return () => {
      cancelled = true;
    };
  }, [seconds]);

  useEffect(() => {
    if (!snap || snap.time == null || snap.time <= lastTime.current) return;
    lastTime.current = snap.time;
    const limit = Math.round(seconds / (snap.interval || 1));
    const values = valuesOf(snap);
    setSeries((prev) => {
      const next: Series = {};
      for (const key of new Set([...Object.keys(prev), ...Object.keys(values)])) {
        const line = [...(prev[key] ?? []), values[key] ?? null];
        next[key] = line.length > limit ? line.slice(line.length - limit) : line;
      }
      return next;
    });
  }, [snap, seconds]);

  return series;
}
