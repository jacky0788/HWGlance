// 卡片外框：一個硬體一張。id 給總覽磚塊用（點磚塊捲到這裡並閃一下）。
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface Props {
  anchor?: string;
  icon: LucideIcon;
  title: string;
  sub?: string;
  children: ReactNode;
}

export function anchorId(deviceId: string): string {
  return `dev-${deviceId}`;
}

export function Card({ anchor, icon: Icon, title, sub, children }: Props) {
  return (
    <section id={anchor ? anchorId(anchor) : undefined}
      className="rounded-2xl border border-line bg-surface p-4 min-w-0">
      <header className="flex items-center gap-2 mb-3 min-w-0">
        <Icon className="size-4 shrink-0 text-fg-subtle" aria-hidden />
        <h3 className="m-0 text-sm font-semibold truncate" title={title}>{title}</h3>
        {sub ? <span className="text-xs text-fg-subtle truncate">{sub}</span> : null}
      </header>
      {children}
    </section>
  );
}
