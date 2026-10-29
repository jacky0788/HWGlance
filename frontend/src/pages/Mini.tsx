// 迷你視窗（置頂、無邊框、可拖曳）：CPU、GPU 的溫度與負載，底下一行報告其他硬體裡最需要注意的那個。
// 拖曳區用 pywebview 的 .pywebview-drag-region；按鈕放在拖曳區外面，才不會一按就被當成拖曳。
import { Maximize2, X } from "lucide-react";
import type { ReactNode } from "react";
import { closeWindow, switchView, useDesktop } from "../lib/bridge";
import { temp } from "../lib/format";
import { useUnit } from "../lib/prefs";
import type { Device, Level, Snapshot, Tile } from "../types";

const SEVERITY: Record<Level, number> = { hot: 2, warm: 1, ok: 0, none: -1 };

function loadOf(devices: Device[], kind: string): number | null {
  return devices.find((d) => d.kind === kind)?.stats.find((s) => s.key === "load")?.value ?? null;
}

function Cell({ title, tile, load }: { title: string; tile: Tile | undefined; load: number | null }) {
  const unit = useUnit();
  return (
    <div className={`level-${tile?.level ?? "none"} min-w-0 rounded-xl border border-line bg-surface px-3 py-1.5`}>
      <div className="flex items-baseline justify-between text-xs text-fg-subtle">
        <span>{title}</span>
        {load != null ? <span className="num">負載 {Math.round(load)}%</span> : null}
      </div>
      <div className="text-3xl font-semibold num leading-tight text-(--level)">{temp(tile?.value, unit)}</div>
    </div>
  );
}

const iconButton =
  "grid place-items-center size-6 shrink-0 rounded-md text-fg-subtle hover:bg-surface-2 hover:text-fg cursor-pointer";

export function Mini({ snap, offline }: { snap: Snapshot | null; offline: string | null }) {
  const unit = useUnit();
  const desktop = useDesktop();
  const tiles = snap?.overview ?? [];
  const cpu = tiles.find((t) => t.kind === "cpu");
  const gpu = tiles.find((t) => t.kind === "gpu" && t.role === "gpu") ?? tiles.find((t) => t.kind === "gpu");
  const rest = tiles.filter((t) => t.kind !== "cpu" && t.kind !== "gpu");
  const worst = [...rest].sort((a, b) => SEVERITY[b.level] - SEVERITY[a.level] || (b.value ?? 0) - (a.value ?? 0))[0];

  let summary: ReactNode;
  if (offline) summary = <span className="text-hot">{offline}</span>;
  else if (!snap || snap.seq === 0) summary = "正在讀取感測器…";
  else if (worst && SEVERITY[worst.level] > 0)
    summary = (
      <span className={`level-${worst.level} text-(--level) font-semibold`}>
        {worst.level === "hot" ? "過熱" : "偏高"}：{worst.label} {temp(worst.value, unit)}
      </span>
    );
  else if (worst) summary = `其他 ${rest.length} 個都正常・最高 ${worst.label} ${temp(worst.value, unit)}`;
  else summary = "其他硬體沒有回報溫度";

  return (
    <div className="h-full flex flex-col gap-1.5 p-2 bg-page border border-line select-none">
      <div className="pywebview-drag-region grid grid-cols-2 gap-2 cursor-move">
        <Cell title="CPU" tile={cpu} load={loadOf(snap?.devices ?? [], "cpu")} />
        <Cell title="GPU" tile={gpu} load={loadOf(snap?.devices ?? [], "gpu")} />
      </div>
      <div className="flex items-center gap-1 min-w-0">
        <p className="pywebview-drag-region m-0 flex-1 min-w-0 truncate text-xs text-fg-muted cursor-move">{summary}</p>
        <button type="button" onClick={() => void switchView("main")} className={iconButton}
          title="展開主畫面" aria-label="展開主畫面">
          <Maximize2 className="size-3.5" aria-hidden />
        </button>
        {desktop ? (
          <button type="button" onClick={() => void closeWindow()} className={iconButton}
            title="關閉迷你視窗（繼續在系統匣監控）" aria-label="關閉迷你視窗">
            <X className="size-3.5" aria-hidden />
          </button>
        ) : null}
      </div>
    </div>
  );
}
