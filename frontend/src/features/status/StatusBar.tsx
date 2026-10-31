// 頂端列（名稱、即時監控／事件記錄分頁、權限／驅動狀態）、提醒（為什麼有些溫度讀不到）、底部（更新時間、讀取耗時）。
import { Activity, PictureInPicture2, ScrollText, Settings, ShieldAlert, TriangleAlert } from "lucide-react";
import { clockTime } from "../../lib/format";
import type { EventBadge } from "../events/useEvents";
import type { Snapshot } from "../../types";

export type Page = "live" | "events";

function Chip({ ok, children }: { ok: boolean; children: string }) {
  return (
    <span className={`${ok ? "level-ok" : "level-warm"} inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-0.5 text-xs text-fg-muted`}>
      <span aria-hidden className="size-1.5 rounded-full bg-(--level)" />
      {children}
    </span>
  );
}

const barButton =
  "inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-xs text-fg-muted hover:border-line-strong hover:text-fg cursor-pointer";

// 分頁用網址的 #live／#events 切換（App.tsx 的 usePage），重新整理或從迷你視窗切回來都還在同一頁
function PageTabs({ page, badge }: { page: Page; badge: EventBadge | null }) {
  const tab = (active: boolean) =>
    `inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs ${active ? "bg-surface-2 text-fg font-semibold" : "text-fg-muted hover:text-fg"}`;
  return (
    <nav aria-label="頁面" className="inline-flex rounded-lg border border-line p-0.5">
      <a href="#live" aria-current={page === "live" ? "page" : undefined} className={tab(page === "live")}>
        <Activity className="size-3.5" aria-hidden /> 即時監控
      </a>
      <a href="#events" aria-current={page === "events" ? "page" : undefined} className={tab(page === "events")}
        title={badge?.title}>
        <ScrollText className="size-3.5" aria-hidden /> 事件記錄
        {badge ? (
          <span className={`level-${badge.level} rounded-full bg-(--level-soft) px-1.5 font-semibold text-(--level) num`}>
            {badge.count}<span className="sr-only"> 件要注意（最近 7 天）</span>
          </span>
        ) : null}
      </a>
    </nav>
  );
}

export function TopBar({ snap, page, badge, onMini, onSettings }: {
  snap: Snapshot | null;
  page: Page;
  badge: EventBadge | null;
  onMini: () => void;
  onSettings: () => void;
}) {
  const source = snap?.source;
  return (
    <header className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3 border-b border-line bg-surface">
      <svg viewBox="0 0 32 32" className="size-6 shrink-0" aria-hidden>
        <circle cx="16" cy="16" r="12" fill="none" stroke="var(--track)" strokeWidth={5}
          strokeDasharray="56.5 75.4" transform="rotate(135 16 16)" strokeLinecap="round" />
        <circle cx="16" cy="16" r="12" fill="none" stroke="var(--ok)" strokeWidth={5}
          strokeDasharray="36 75.4" transform="rotate(135 16 16)" strokeLinecap="round" />
      </svg>
      <h1 className="m-0 text-base font-semibold">HWGlance</h1>
      <PageTabs page={page} badge={badge} />
      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        {source?.kind === "demo" ? <Chip ok={false}>示範資料</Chip> : null}
        {source?.kind === "lhm" ? (
          <>
            <Chip ok={source.admin}>{source.admin ? "系統管理員" : "一般權限"}</Chip>
            <Chip ok={source.pawnio != null}>{source.pawnio ? `PawnIO ${source.pawnio}` : "PawnIO 未安裝"}</Chip>
          </>
        ) : null}
        <button type="button" onClick={onMini} className={barButton} title="縮成置頂的小視窗">
          <PictureInPicture2 className="size-3.5" aria-hidden /> 迷你模式
        </button>
        <button type="button" onClick={onSettings} className={barButton}>
          <Settings className="size-3.5" aria-hidden /> 設定
        </button>
      </div>
    </header>
  );
}

export function Notes({ notes, offline }: { notes: string[]; offline: string | null }) {
  if (!offline && notes.length === 0) return null;
  return (
    <div className="space-y-2">
      {offline ? (
        <p className="level-hot m-0 flex items-start gap-2 rounded-xl border border-line bg-(--level-soft) px-4 py-2.5 text-sm">
          <ShieldAlert className="size-4 mt-0.5 shrink-0 text-(--level)" aria-hidden />
          {offline}，畫面停在最後一次的讀數。
        </p>
      ) : null}
      {notes.length > 0 ? (
        <ul className="level-warm m-0 list-none rounded-xl border border-line bg-(--level-soft) px-4 py-2.5 text-sm space-y-1">
          {notes.map((n) => (
            <li key={n} className="flex items-start gap-2">
              <TriangleAlert className="size-4 mt-0.5 shrink-0 text-(--level)" aria-hidden />
              {n}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function Footer({ snap }: { snap: Snapshot }) {
  const parts = [
    `每 ${snap.interval} 秒更新`,
    snap.perf.readMs != null ? `讀取 ${snap.perf.readMs} ms` : null,
    snap.source.kind === "lhm" && snap.source.version ? `LibreHardwareMonitor ${snap.source.version}` : null,
    `最後更新 ${clockTime(snap.time)}`,
  ].filter(Boolean);
  return <footer className="px-5 pb-4 text-xs text-fg-subtle num">{parts.join("・")}</footer>;
}
