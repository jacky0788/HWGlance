// 點總覽磚塊、卡片或硬碟那一列跳出的「詳細資訊」：規格＋所有讀數，分段、中文、附說明，開著時跟著快照一起更新。
// 內容由後端 src/hwglance/sensors/details.py 整理好（GET /api/device?id=），這裡只負責排版。
import { CircuitBoard, Cpu, Droplets, Gpu, HardDrive, MemoryStick, Thermometer, X, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { number, temp, tempExact } from "../../lib/format";
import { useUnit } from "../../lib/prefs";
import type { DetailBars, DetailRow, DetailSection, DetailTable, Device, DeviceDetail, Kind } from "../../types";

const ICONS: Record<Kind, LucideIcon> = {
  cpu: Cpu, gpu: Gpu, board: CircuitBoard, cooler: Droplets, memory: MemoryStick, storage: HardDrive, psu: Zap,
  other: Thermometer,
};

function RowValue({ row }: { row: DetailRow }) {
  const unit = useUnit();
  if (row.temp && typeof row.value === "number") {
    const range = [row.min != null ? `最低 ${temp(row.min, unit)}` : null, row.max != null ? `最高 ${temp(row.max, unit)}` : null]
      .filter(Boolean)
      .join("・");
    return (
      <span className={`level-${row.level ?? "none"}`}>
        <span className={`font-semibold ${row.level ? "text-(--level)" : ""}`}>{tempExact(row.value, unit)}</span>
        {range ? <span className="block text-xs text-fg-subtle whitespace-nowrap">{range}</span> : null}
      </span>
    );
  }
  if (typeof row.value === "number") {
    return (
      <span className="font-medium">
        {number(row.value)}
        {row.unit ? <span className="ml-1 text-xs text-fg-subtle">{row.unit}</span> : null}
      </span>
    );
  }
  return <span className="font-medium">{row.value ?? "—"}</span>;
}

function Rows({ rows }: { rows: DetailRow[] }) {
  return (
    <dl className="m-0">
      {rows.map((row) => (
        <div key={row.label} className="flex items-start justify-between gap-4 py-2 border-t border-line first:border-t-0">
          <dt className="min-w-0">
            <span className="text-sm text-fg-muted">{row.label}</span>
            {row.note ? <span className="block text-xs text-fg-subtle">{row.note}</span> : null}
          </dt>
          <dd className="m-0 max-w-[60%] text-right text-sm num break-words">
            <RowValue row={row} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Bars({ bars }: { bars: DetailBars }) {
  return (
    <div className="mt-3">
      <p className="m-0 mb-2 text-xs text-fg-subtle">{bars.label}</p>
      <ul className="m-0 p-0 list-none grid grid-cols-[repeat(auto-fill,minmax(4.75rem,1fr))] gap-x-3 gap-y-2.5">
        {bars.items.map((item) => (
          <li key={item.label} className="min-w-0" title={item.label}>
            <div className="flex items-baseline justify-between gap-1 text-xs">
              <span className="text-fg-muted truncate">{item.label}</span>
              <span className="num">{number(item.value)}{bars.unit}</span>
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-track overflow-hidden">
              <div className="h-full rounded-full bg-accent transition-[width] duration-500"
                style={{ width: `${Math.min(100, Math.max(0, ((item.value ?? 0) / bars.max) * 100))}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Table({ table }: { table: DetailTable }) {
  // 數字欄靠右、文字欄（廠牌、型號、狀態）靠左
  const numeric = table.columns.map((_, i) => table.rows.some((r) => typeof r.cells[i] === "number"));
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            <th scope="col" className="sr-only">項目</th>
            {table.columns.map((c, i) => (
              <th key={c.label} scope="col"
                className={`px-2 pb-1 text-xs font-normal text-fg-subtle whitespace-nowrap ${numeric[i] ? "text-right" : "text-left"}`}>
                {c.label}{c.unit ? `（${c.unit}）` : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r) => (
            <tr key={r.label} className="border-t border-line">
              <th scope="row" className="py-1.5 pr-2 text-left font-normal text-fg-muted whitespace-nowrap">{r.label}</th>
              {r.cells.map((cell, i) => (
                <td key={table.columns[i]?.label ?? i}
                  className={`px-2 py-1.5 ${numeric[i] ? "text-right num" : "text-left"} ${cell === "沒接" || cell === "（空）" ? "text-fg-subtle" : ""}`}>
                  {typeof cell === "number" ? number(cell) : (cell ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ section }: { section: DetailSection }) {
  return (
    <section>
      <h3 className="m-0 text-sm font-semibold">{section.title}</h3>
      {section.note ? <p className="mt-0.5 mb-0 text-xs text-fg-subtle">{section.note}</p> : null}
      {section.rows ? <div className="mt-1"><Rows rows={section.rows} /></div> : null}
      {section.bars ? <Bars bars={section.bars} /> : null}
      {section.table ? <Table table={section.table} /> : null}
    </section>
  );
}

export function DetailDialog({ deviceId, device, interval, onClose }: {
  deviceId: string | null;
  device: Device | null; // 快照裡的同一個硬體：詳細資料還沒到之前，標題先用它
  interval: number;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<DeviceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (deviceId && !dialog.open) dialog.showModal();
    if (!deviceId && dialog.open) dialog.close();
  }, [deviceId]);

  useEffect(() => {
    if (!deviceId) return;
    let cancelled = false;
    let timer: number | undefined;
    setData(null);
    setError(null);
    if (body.current) body.current.scrollTop = 0;
    const load = async () => {
      if (!document.hidden) {
        try {
          const d = await api.device(deviceId);
          if (!cancelled) {
            setData(d);
            setError(null);
          }
        } catch (e) {
          if (!cancelled) setError(errorMessage(e));
        }
      }
      if (!cancelled) timer = window.setTimeout(() => void load(), Math.max(1, interval) * 1000);
    };
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [deviceId, interval]);

  const kind = data?.kind ?? device?.kind ?? "other";
  const Icon = ICONS[kind] ?? Thermometer;
  const title = data?.name ?? device?.name ?? "";
  const sub = data?.sub ?? device?.sub ?? "";

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="detail-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose(); // 點視窗外面（背景）關掉
      }}
      className={`kind-${kind} m-auto w-[min(46rem,94vw)] max-h-[90vh] rounded-2xl border kind-frame bg-surface text-fg p-0 backdrop:bg-black/50`}>
      <div className="flex max-h-[90vh] flex-col">
        <header className="flex items-center gap-3 px-5 pt-4 pb-3 border-b border-line">
          <Icon className="size-5 shrink-0 text-fg-subtle" aria-hidden />
          <div className="min-w-0">
            <h2 id="detail-title" className="m-0 text-base font-semibold truncate" title={title}>{title}</h2>
            {sub ? <p className="m-0 text-xs text-fg-subtle truncate">{sub}</p> : null}
          </div>
          <span className="flex-1" />
          <button type="button" onClick={onClose} aria-label="關閉"
            className="shrink-0 rounded-lg p-1.5 text-fg-muted hover:bg-surface-2 hover:text-fg cursor-pointer">
            <X className="size-4" aria-hidden />
          </button>
        </header>
        <div ref={body} className="overflow-y-auto px-5 py-4 space-y-6">
          {error && data ? <p role="alert" className="m-0 text-xs text-warm">更新失敗：{error}（顯示的是上一次的數字）</p> : null}
          {data == null ? (
            <p className="m-0 py-6 text-sm text-fg-muted text-center">{error ?? "讀取中…"}</p>
          ) : (
            data.sections.map((s) => <Section key={s.title} section={s} />)
          )}
        </div>
      </div>
    </dialog>
  );
}
