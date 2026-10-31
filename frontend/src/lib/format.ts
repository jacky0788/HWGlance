// 數字怎麼顯示。溫度一律取整數（一眼看，不需要小數）；滑過看得到小數一位。
// 後端的溫度都是 °C，顯示時才依設定換成 °F（門檻與顏色不受影響）。
import type { Kind, Stat, Unit } from "../types";

const int = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });
const one = new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const plain = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 3 });

// 詳細資訊的數字：後端已經取好位數，這裡只加千分位（25836 → 25,836）
export function number(v: number | null | undefined): string {
  return v == null ? "—" : plain.format(v);
}

export function toUnit(celsius: number, unit: Unit): number {
  return unit === "F" ? (celsius * 9) / 5 + 32 : celsius;
}

export function fromUnit(value: number, unit: Unit): number {
  return unit === "F" ? ((value - 32) * 5) / 9 : value;
}

export function temp(v: number | null | undefined, unit: Unit = "C"): string {
  return v == null ? "—" : `${Math.round(toUnit(v, unit))}°`;
}

export function tempExact(v: number | null | undefined, unit: Unit = "C"): string {
  return v == null ? "—" : `${one.format(toUnit(v, unit))}°${unit}`;
}

export function size(gb: number): string {
  return gb >= 1000 ? `${(gb / 1024).toFixed(2)} TB` : `${int.format(gb)} GB`;
}

export interface Shown {
  value: string;
  unit: string;
  note?: string;
}

export function stat(s: Stat, kind: Kind): Shown {
  switch (s.unit) {
    case "MHz":
      return kind === "cpu" && s.value >= 1000
        ? { value: (s.value / 1000).toFixed(2), unit: "GHz" }
        : { value: int.format(s.value), unit: "MHz" };
    case "MB": {
      const total = s.total != null ? ` / ${one.format(s.total / 1024)}` : "";
      return { value: `${one.format(s.value / 1024)}${total}`, unit: "GB" };
    }
    case "GB": {
      const total = s.total != null ? ` / ${one.format(s.total)}` : "";
      return { value: `${one.format(s.value)}${total}`, unit: "GB" };
    }
    default:
      return { value: int.format(s.value), unit: s.unit, note: s.note };
  }
}

export function clockTime(epochSeconds: number | null): string {
  if (epochSeconds == null) return "—";
  return new Date(epochSeconds * 1000).toLocaleTimeString("zh-TW", { hour12: false });
}

// ---- 事件記錄的時間（本地時間）：「今天 10:10–10:11」「8/22 11:08–14:01」，不是今年才寫年份 ----

const pad = (n: number) => String(n).padStart(2, "0");

export function hm(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function hms(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${hm(epoch)}:${pad(d.getSeconds())}`;
}

// now 用後端產生報告的時間，「今天」「昨天」才跟資料對得上
export function day(epoch: number, now: number): string {
  const d = new Date(epoch * 1000);
  const n = new Date(now * 1000);
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const ago = Math.round((midnight(n) - midnight(d)) / 86_400_000);
  if (ago === 0) return "今天";
  if (ago === 1) return "昨天";
  const md = `${d.getMonth() + 1}/${d.getDate()}`;
  return d.getFullYear() === n.getFullYear() ? md : `${d.getFullYear()}/${md}`;
}

export function when(epoch: number, now: number): string {
  return `${day(epoch, now)} ${hm(epoch)}`;
}

export function span(start: number, end: number, now: number): string {
  const a = when(start, now);
  const b = when(end, now);
  if (a === b) return a;
  return day(start, now) === day(end, now) ? `${a}–${hm(end)}` : `${a} – ${b}`;
}

export function daySpan(start: number, end: number, now: number): string {
  const a = day(start, now);
  const b = day(end, now);
  return a === b ? a : `${a}–${b}`;
}

export function fullDate(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

// 相隔多久：跟後端 events/incidents.py 的 _gap 一樣的寫法
export function gap(seconds: number): string {
  if (seconds < 120) return `${Math.round(seconds)} 秒`;
  if (seconds < 7200) return `${Math.round(seconds / 60)} 分鐘`;
  if (seconds < 172_800) return `${Math.round(seconds / 3600)} 小時`;
  return `${Math.round(seconds / 86400)} 天`;
}
