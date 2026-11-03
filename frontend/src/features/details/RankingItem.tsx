// 「顯存被誰佔用」排行的一行：名次、名稱、用量、佔總量的長條；有 process 的多一顆「關閉」（SPEC D31）。
// 關閉的流程（先問、原地確認、關不掉才問強制結束）在 features/processes/CloseProcess.tsx，暫存檔頁也用同一套。
import { number } from "../../lib/format";
import type { DetailRanking } from "../../types";
import { CloseButton, ClosePanel, useCloseProcess } from "../processes/CloseProcess";

type Item = DetailRanking["items"][number];

export function RankingItem({ item, index, total }: { item: Item; index: number; total: boolean }) {
  const flow = useCloseProcess(item.process);
  return (
    <li className="min-w-0">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 truncate" title={item.label}>
          <span className="mr-1.5 num text-fg-subtle">{index + 1}.</span>{item.label}
        </span>
        <span className="shrink-0 flex items-baseline gap-2">
          <span className="num font-medium text-(--m)">
            {number(item.value)}<span className="ml-1 text-xs text-fg-subtle">{item.unit}</span>
          </span>
          {item.process ? <CloseButton flow={flow} label={item.label} /> : null}
        </span>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <div className="h-1.5 flex-1 rounded-full bg-track overflow-hidden">
          <div className="h-full rounded-full bg-(--m) transition-[width] duration-500"
            style={{ width: `${Math.max(1, Math.min(100, item.share * 100))}%` }} />
        </div>
        <span className="shrink-0 text-xs text-fg-subtle num">
          {total ? `${item.share < 0.01 ? "<1" : Math.round(item.share * 100)}%・` : ""}{item.note}
        </span>
      </div>
      <ClosePanel flow={flow} />
    </li>
  );
}
