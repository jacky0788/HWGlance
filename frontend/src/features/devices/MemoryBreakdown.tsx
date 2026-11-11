// 記憶體卡片的組成長條（使用中、已壓縮、待寫回、待命快取、完全空著）與「釋放記憶體」（SPEC D32，後端 memory.py）。
// 每 5 秒抓 /api/memory（畫面看不到就不抓）；按「釋放」先在卡片裡確認，結果把「可用」與「完全空著」分開寫（清待命快取只會讓後者變多）。
// 卡片整張可以點開詳細資訊，所以這裡的點擊一律 stopPropagation。
// Mac（後端 sensors/mac.py 的 MacMemory）：名稱照活動監視器、上面多一行記憶體壓力、沒有釋放按鈕。
import { Eraser } from "lucide-react";
import { useEffect, useState } from "react";
import type { MouseEvent } from "react";
import { api, errorMessage } from "../../lib/api";
import type { MemoryRelease, MemorySegmentKey, MemoryStatus } from "../../types";

const POLL_MS = 5000;
const GB = 2 ** 30;
const SEGMENT_COLOR: Record<MemorySegmentKey, string> = {
  used: "bg-(--kind)",
  compressed: "bg-(--mem-compressed)",
  modified: "bg-(--mem-modified)",
  standby: "bg-(--mem-standby)",
  free: "bg-(--mem-free)",
  wired: "bg-(--mem-wired)",
};
const PRESSURE: Record<string, { text: string; level: string; note: string }> = {
  normal: { text: "正常", level: "level-ok", note: "記憶體夠用" },
  warn: { text: "偏高", level: "level-warm", note: "macOS 開始大量壓縮、可能用到交換空間，程式可能變慢" },
  critical: { text: "很高", level: "level-hot", note: "記憶體不夠用了，關掉一些不用的程式" },
};

// Mac：活動監視器判斷夠不夠用看的是「記憶體壓力」，不是已用幾 %（已壓縮也算已用，所以常常看起來偏高）
function Pressure({ p }: { p: NonNullable<MemoryStatus["pressure"]> }) {
  const shown = p.level ? PRESSURE[p.level] : null;
  if (!shown) return null;
  return (
    <p className={`${shown.level} m-0 mb-2 flex items-center gap-1.5 text-xs`} title={shown.note}>
      <span aria-hidden className="size-2 shrink-0 rounded-full bg-(--level)" />
      <span className="text-fg-muted">記憶體壓力</span>
      <span className="font-semibold text-(--level)">{shown.text}</span>
      {p.free != null ? <span className="text-fg-subtle num">・系統還剩 {p.free}%</span> : null}
    </p>
  );
}
const small = "rounded-md border px-2 py-0.5 text-xs cursor-pointer disabled:opacity-50";
const plain = `${small} border-line text-fg-muted hover:text-fg hover:border-line-strong`;

const gb = (bytes: number) => (bytes / GB).toFixed(1);
const stop = (e: MouseEvent) => {
  e.stopPropagation();
};

// enabled＝false：這台沒有記憶體組成（Mac 版），不抓
export function useMemoryStatus(enabled = true): [MemoryStatus | null, () => void] {
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      if (!document.hidden) {
        try {
          const s = await api.memory();
          if (!cancelled) setStatus(s);
        } catch {
          if (!cancelled) setStatus(null); // 這個模式沒有（404）或本體關了：卡片退回只有用量條
        }
      }
      if (!cancelled) timer = window.setTimeout(() => void load(), POLL_MS);
    };
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [tick, enabled]);
  return [enabled ? status : null, () => setTick((t) => t + 1)];
}

function ReleaseResult({ r }: { r: MemoryRelease }) {
  return (
    <div className="space-y-0.5">
      <p className="m-0 text-ok">
        可用 {gb(r.before)} → {gb(r.after)} GB{r.freed > 0 ? `（多 ${gb(r.freed)} GB）` : ""}・完全空著 {gb(r.freeBefore)} → {gb(r.freeAfter)} GB
      </p>
      <p className="m-0 text-fg-subtle">
        收回 {r.trimmed} 個程式的記憶體{r.skippedForeground ? "（跳過前景的程式）" : ""}
        {r.standby === "purged" ? "・待命快取已清" : r.standby === "needs_admin" ? "・待命快取沒清：要用系統管理員執行 HWGlance" : "・清待命快取失敗"}
      </p>
    </div>
  );
}

export function MemoryBreakdown({ status, refresh }: { status: MemoryStatus; refresh: () => void }) {
  const [step, setStep] = useState<"idle" | "confirm" | "working">("idle");
  const [result, setResult] = useState<MemoryRelease | null>(null);
  const [error, setError] = useState<string | null>(null);

  const release = async () => {
    setStep("working");
    setError(null);
    try {
      setResult(await api.releaseMemory());
      refresh();
    } catch (e) {
      setError(errorMessage(e));
    }
    setStep("idle");
  };

  const shown = status.segments.filter((s) => s.bytes > 0);
  const mac = status.pressure != null;
  return (
    <div onClick={stop}>
      {status.pressure ? <Pressure p={status.pressure} /> : null}
      <div className="flex h-2.5 rounded-full bg-track overflow-hidden" role="img"
        aria-label={shown.map((s) => `${s.label} ${gb(s.bytes)} GB`).join("、")}>
        {shown.map((s) => (
          <div key={s.key} className={`h-full ${SEGMENT_COLOR[s.key]} transition-[width] duration-500`} title={`${s.label}：${s.note}`}
            style={{ width: `${(s.bytes / status.total) * 100}%` }} />
        ))}
      </div>
      <ul className="m-0 mt-2 p-0 list-none grid grid-cols-[repeat(auto-fill,minmax(6.5rem,1fr))] gap-x-3 gap-y-1 text-xs">
        {status.segments.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5 min-w-0" title={s.note}>
            <span aria-hidden className={`size-2 shrink-0 rounded-full ${SEGMENT_COLOR[s.key]}`} />
            <span className="text-fg-muted truncate">{s.label}</span>
            <span className="ml-auto num">
              {gb(s.bytes)}<span className="ml-0.5 text-fg-subtle">GB</span>
              {s.key === "compressed" && status.compression.stored
                ? <span className="ml-0.5 text-fg-subtle">（{gb(status.compression.stored)}）</span>
                : null}
            </span>
          </li>
        ))}
      </ul>
      <p className="m-0 mt-1.5 text-xs text-fg-subtle">
        {mac
          ? `可用 ${gb(status.available)} GB（快取的檔案＋空著）・已壓縮的 ${gb(status.compression.bytes)} GB 裡是 ${gb(status.compression.stored ?? 0)} GB 的資料`
          : `可用 ${gb(status.available)} GB（待命快取＋完全空著）・Windows 記憶體壓縮${status.compression.enabled ? `開著（佔 ${gb(status.compression.bytes)} GB）` : "沒開"}`}
      </p>

      {status.canRelease === false ? null : <div className="mt-2.5 text-xs">
        {step === "confirm" ? (
          <div role="alertdialog" aria-label="確認釋放記憶體" className="rounded-lg border border-line bg-surface-2 px-3 py-2 space-y-1.5">
            <p className="m-0 text-fg-muted">
              會收回各程式暫時沒在用的記憶體（目前在前景的程式會跳過）
              {status.canPurge ? "，並清掉待命快取" : "；待命快取要用系統管理員執行 HWGlance 才清得掉"}。
            </p>
            <p className="m-0 text-fg-subtle">「可用」會變多，但程式用到時要重新載入；遊戲中按可能短暫卡頓。</p>
            <div className="flex gap-2 pt-0.5">
              <button type="button" className={plain} onClick={() => void release()}>釋放</button>
              <button type="button" className={plain} onClick={() => setStep("idle")}>取消</button>
            </div>
          </div>
        ) : (
          <button type="button" className={`${plain} inline-flex items-center gap-1`} disabled={step === "working"}
            onClick={() => {
              setStep("confirm");
              setResult(null);
              setError(null);
            }}>
            <Eraser className="size-3" aria-hidden /> {step === "working" ? "釋放中…" : "釋放記憶體"}
          </button>
        )}
        {result ? <div role="status" className="mt-2"><ReleaseResult r={result} /></div> : null}
        {error ? <p role="alert" className="m-0 mt-2 text-warm">{error}</p> : null}
      </div>}
    </div>
  );
}
