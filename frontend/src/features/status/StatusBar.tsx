// 頂端列（名稱＋權限／驅動狀態）、提醒（為什麼有些溫度讀不到）、底部（更新時間、讀取耗時）。
import { PictureInPicture2, Settings, ShieldAlert, TriangleAlert } from "lucide-react";
import { clockTime } from "../../lib/format";
import type { Snapshot } from "../../types";

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

export function TopBar({ snap, onMini, onSettings }: {
  snap: Snapshot | null;
  onMini: () => void;
  onSettings: () => void;
}) {
  const source = snap?.source;
  return (
    <header className="flex items-center gap-3 px-5 py-3 border-b border-line bg-surface">
      <svg viewBox="0 0 32 32" className="size-6 shrink-0" aria-hidden>
        <circle cx="16" cy="16" r="12" fill="none" stroke="var(--track)" strokeWidth={5}
          strokeDasharray="56.5 75.4" transform="rotate(135 16 16)" strokeLinecap="round" />
        <circle cx="16" cy="16" r="12" fill="none" stroke="var(--ok)" strokeWidth={5}
          strokeDasharray="36 75.4" transform="rotate(135 16 16)" strokeLinecap="round" />
      </svg>
      <h1 className="m-0 text-base font-semibold">HWGlance</h1>
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
