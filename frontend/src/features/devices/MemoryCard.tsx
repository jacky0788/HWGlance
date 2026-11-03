// 記憶體：組成長條＋「釋放記憶體」（MemoryBreakdown，讀 /api/memory）；那個抓不到時退回只有用量條。
// 有的話再列每條 DIMM 的溫度（DDR4 大多沒有溫度感測器）。
import { MemoryStick } from "lucide-react";
import { Card } from "../../components/Card";
import { TempRow } from "../../components/Readout";
import type { Device } from "../../types";
import { MemoryBreakdown, useMemoryStatus } from "./MemoryBreakdown";

export function MemoryCard({ device, onOpen }: { device: Device; onOpen: (id: string) => void }) {
  const used = device.stats.find((s) => s.key === "used");
  const load = device.stats.find((s) => s.key === "load");
  const percent = load?.value ?? (used?.total ? (used.value / used.total) * 100 : null);
  const [status, refresh] = useMemoryStatus();
  return (
    <Card kind="memory" icon={MemoryStick} title={device.name} onOpen={() => onOpen(device.id)}
      sub={used?.total != null ? `${used.total.toFixed(1)} GB` : undefined}>
      {percent != null ? (
        <>
          {status == null ? (
            <div className="h-2 rounded-full bg-track overflow-hidden" role="meter" aria-valuemin={0}
              aria-valuemax={100} aria-valuenow={Math.round(percent)} aria-label="記憶體使用率">
              <div className="h-full rounded-full bg-(--kind) transition-[width] duration-500"
                style={{ width: `${Math.min(100, percent)}%` }} />
            </div>
          ) : null}
          <p className={`m-data mb-0 flex justify-between text-sm num ${status == null ? "mt-2" : "mt-0 mb-2"}`}>
            <span className="text-fg-muted">
              已用 <span className="font-medium text-(--m)">{used ? used.value.toFixed(1) : "—"}</span>
              {used ? <span className="ml-1 text-xs text-fg-subtle">GB</span> : null}
            </span>
            <span className="font-semibold text-(--m)">{Math.round(percent)}%</span>
          </p>
        </>
      ) : (
        <p className="m-0 text-sm text-fg-muted">讀不到用量</p>
      )}
      {status != null ? <MemoryBreakdown status={status} refresh={refresh} /> : null}
      {device.temps.length > 0 ? (
        <ul className="mt-3 mb-0 p-0 list-none">{device.temps.map((t) => <TempRow key={t.id} t={t} />)}</ul>
      ) : (
        <p className="mt-3 mb-0 text-xs text-fg-subtle">記憶體沒有回報溫度（DDR4 大多沒有溫度感測器）</p>
      )}
    </Card>
  );
}
