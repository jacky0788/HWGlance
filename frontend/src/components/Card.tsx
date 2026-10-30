// 卡片外框：一個硬體一張。給了 onOpen 的卡片整張都可以點，打開那個硬體的詳細資訊；
// 標題列的「詳細」是給鍵盤與螢幕報讀用的按鈕（點了一樣往上傳到卡片）。
import { ChevronRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { Kind } from "../types";

interface Props {
  kind: Kind; // 種類色：圖示、儀表、走勢（溫度數字不受影響）
  icon: LucideIcon;
  title: string;
  sub?: string;
  onOpen?: () => void;
  children: ReactNode;
}

export function Card({ kind, icon: Icon, title, sub, onOpen, children }: Props) {
  return (
    <section onClick={onOpen}
      className={`kind-${kind} rounded-2xl border border-line bg-surface p-4 min-w-0 ${onOpen ? "cursor-pointer hover:border-line-strong transition-colors" : ""}`}>
      <header className="flex items-center gap-2 mb-3 min-w-0">
        <Icon className="size-4 shrink-0 text-(--kind)" aria-hidden />
        <h3 className="m-0 text-sm font-semibold truncate" title={title}>{title}</h3>
        {sub ? <span className="text-xs text-fg-subtle truncate">{sub}</span> : null}
        {onOpen ? (
          <button type="button" aria-label={`${title}：詳細資訊`}
            className="ml-auto shrink-0 flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs text-fg-subtle hover:text-fg cursor-pointer">
            詳細<ChevronRight className="size-3.5" aria-hidden />
          </button>
        ) : null}
      </header>
      {children}
    </section>
  );
}
