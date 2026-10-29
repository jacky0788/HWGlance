// 小折線圖：最近幾分鐘的走勢。沒有值（null）的地方斷開；上下範圍至少 10 度，免得雜訊被放大成波浪。
import { temp } from "../lib/format";
import { useUnit } from "../lib/prefs";
import type { Level } from "../types";

interface Props {
  values: (number | null)[];
  level: Level;
  minSpan?: number;
  label: string;
}

export function Sparkline({ values, level, minSpan = 10, label }: Props) {
  const unit = useUnit();
  const nums = values.filter((v): v is number => v != null);
  if (nums.length < 2) {
    return <div className="h-8 text-xs text-fg-subtle flex items-center">{label}：資料累積中</div>;
  }
  const lowest = Math.min(...nums);
  const highest = Math.max(...nums);
  let lo = lowest;
  let hi = highest;
  if (hi - lo < minSpan) {
    const mid = (hi + lo) / 2;
    lo = mid - minSpan / 2;
    hi = mid + minSpan / 2;
  }
  const n = values.length;
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v == null) {
      pen = false;
      return;
    }
    const x = n === 1 ? 100 : (i / (n - 1)) * 100;
    const y = 22 - ((v - lo) / (hi - lo)) * 20;
    d += `${pen ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
    pen = true;
  });
  return (
    <figure className={`level-${level} m-0`}>
      <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="block w-full h-8" role="img"
        aria-label={`${label}：最低 ${temp(lowest, unit)}、最高 ${temp(highest, unit)}`}>
        <path d={d} fill="none" stroke="var(--level)" strokeWidth={1.5} vectorEffect="non-scaling-stroke"
          strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <figcaption className="flex justify-between text-xs text-fg-subtle num">
        <span>{label}</span>
        <span>
          {temp(lowest, unit)} ～ {temp(highest, unit)}
        </span>
      </figcaption>
    </figure>
  );
}
