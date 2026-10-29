// 卡片裡的一格數值：上面小標題、下面數字。溫度用等級的顏色。
import { stat as showStat, temp, tempExact } from "../lib/format";
import { useUnit } from "../lib/prefs";
import type { Kind, Stat, Temp } from "../types";

export function StatReadout({ stat, kind }: { stat: Stat; kind: Kind }) {
  const shown = showStat(stat, kind);
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-subtle">{stat.label}</dt>
      <dd className="m-0 num font-medium">
        {shown.value} <span className="text-xs text-fg-subtle">{shown.unit}</span>
        {shown.note ? <span className="text-xs text-fg-subtle">・{shown.note}</span> : null}
      </dd>
    </div>
  );
}

export function TempReadout({ t }: { t: Temp }) {
  const unit = useUnit();
  return (
    <div className={`level-${t.level} min-w-0`} title={`最低 ${tempExact(t.min, unit)}、最高 ${tempExact(t.max, unit)}`}>
      <dt className="text-xs text-fg-subtle truncate">{t.label}</dt>
      <dd className="m-0 num font-medium text-(--level)">{temp(t.value, unit)}</dd>
    </div>
  );
}

// 清單的一列：左邊名稱、右邊數字（主機板溫度、記憶體）
export function TempRow({ t }: { t: Temp }) {
  const unit = useUnit();
  return (
    <li className={`level-${t.level} flex items-baseline justify-between gap-3 py-1.5 border-t border-line first:border-t-0`}>
      <span className="text-sm text-fg-muted truncate">{t.label}</span>
      <span className="num text-sm">
        <span className="font-semibold text-(--level)">{temp(t.value, unit)}</span>
        <span className="ml-2 text-xs text-fg-subtle">最高 {temp(t.max, unit)}</span>
      </span>
    </li>
  );
}
