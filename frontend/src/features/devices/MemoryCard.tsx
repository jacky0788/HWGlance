// 記憶體：用量條＋（有的話）每條 DIMM 的溫度。DDR4 大多沒有溫度感測器。
import { MemoryStick } from "lucide-react";
import { Card } from "../../components/Card";
import { TempRow } from "../../components/Readout";
import type { Device } from "../../types";

export function MemoryCard({ device, onOpen }: { device: Device; onOpen: (id: string) => void }) {
  const used = device.stats.find((s) => s.key === "used");
  const load = device.stats.find((s) => s.key === "load");
  const percent = load?.value ?? (used?.total ? (used.value / used.total) * 100 : null);
  return (
    <Card kind="memory" icon={MemoryStick} title={device.name} onOpen={() => onOpen(device.id)}
      sub={used?.total != null ? `${used.total.toFixed(1)} GB` : undefined}>
      {percent != null ? (
        <>
          <div className="h-2 rounded-full bg-track overflow-hidden" role="meter" aria-valuemin={0}
            aria-valuemax={100} aria-valuenow={Math.round(percent)} aria-label="記憶體使用率">
            <div className="h-full rounded-full bg-(--kind) transition-[width] duration-500"
              style={{ width: `${Math.min(100, percent)}%` }} />
          </div>
          <p className="mt-2 mb-0 flex justify-between text-sm num">
            <span className="text-fg-muted">
              已用 {used ? `${used.value.toFixed(1)} GB` : "—"}
            </span>
            <span className="font-semibold">{Math.round(percent)}%</span>
          </p>
        </>
      ) : (
        <p className="m-0 text-sm text-fg-muted">讀不到用量</p>
      )}
      {device.temps.length > 0 ? (
        <ul className="mt-3 mb-0 p-0 list-none">{device.temps.map((t) => <TempRow key={t.id} t={t} />)}</ul>
      ) : (
        <p className="mt-3 mb-0 text-xs text-fg-subtle">記憶體沒有回報溫度（DDR4 大多沒有溫度感測器）</p>
      )}
    </Card>
  );
}
