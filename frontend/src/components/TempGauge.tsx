// 弧形溫度儀表（270°）。顏色跟著等級；刻度預設 20～100°C（換成 °F 只改數字，弧長不變）。
import type { Level } from "../types";
import { temp, tempExact } from "../lib/format";
import { useUnit } from "../lib/prefs";

const R = 40;
const CIRCUMFERENCE = 2 * Math.PI * R;
const ARC = CIRCUMFERENCE * 0.75;

interface Props {
  value: number | null;
  level: Level;
  label: string;
  low?: number;
  high?: number;
}

export function TempGauge({ value, level, label, low = 20, high = 100 }: Props) {
  const unit = useUnit();
  const fraction = value == null ? 0 : Math.min(1, Math.max(0, (value - low) / (high - low)));
  return (
    <svg viewBox="0 0 100 100" className={`level-${level} block w-full h-auto`} role="img"
      aria-label={`${label} ${tempExact(value, unit)}`}>
      <circle cx="50" cy="50" r={R} className="gauge-track" strokeWidth={8}
        strokeDasharray={`${ARC} ${CIRCUMFERENCE}`} transform="rotate(135 50 50)" />
      <circle cx="50" cy="50" r={R} className="gauge-value" strokeWidth={8}
        strokeDasharray={`${ARC * fraction} ${CIRCUMFERENCE}`} transform="rotate(135 50 50)" />
      <text x="50" y="56" textAnchor="middle" fontSize={24} className="gauge-number">{temp(value, unit)}</text>
      <text x="50" y="74" textAnchor="middle" fontSize={10} className="gauge-label">{label}</text>
    </svg>
  );
}
