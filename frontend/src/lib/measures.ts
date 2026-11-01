// 數值量的是什麼 → 圖示與顏色（index.css 的 .m-*）。卡片的數值、詳細資訊的段落共用，
// 負載、時脈、功耗、風扇、容量一眼分得開；溫度不在這裡上色，照等級的綠黃紅。
import { Activity, ArrowLeftRight, Fan, Gauge, Info, Layers, Thermometer, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Measure } from "../types";

export type MeasureOrNone = Measure | "none";

export const MEASURE_ICONS: Record<MeasureOrNone, LucideIcon> = {
  temp: Thermometer, load: Activity, clock: Gauge, power: Zap, fan: Fan, data: Layers, flow: ArrowLeftRight, none: Info,
};

/** 數值要不要用種類色：溫度照等級、規格類照原本的字色 */
export function colored(m: MeasureOrNone | undefined): boolean {
  return m != null && m !== "temp" && m !== "none";
}

// 快照 stats 的 key（後端 normalize.py）→ 量的是什麼
const STAT_MEASURES: Record<string, Measure> = {
  load: "load", activity: "load", clock: "clock", power: "power", fan: "fan", vram: "data", used: "data", free: "data",
};

export function statMeasure(key: string): MeasureOrNone {
  return STAT_MEASURES[key] ?? "none";
}
