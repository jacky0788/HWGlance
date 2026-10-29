// 數字怎麼顯示。溫度一律取整數（一眼看，不需要小數）；滑過看得到小數一位。
// 後端的溫度都是 °C，顯示時才依設定換成 °F（門檻與顏色不受影響）。
import type { Kind, Stat, Unit } from "../types";

const int = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });
const one = new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

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
