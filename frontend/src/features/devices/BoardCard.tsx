// 主機板（含水冷頭、電源、其他）：溫度清單＋有在轉的風扇。
import { CircuitBoard, Droplets, Fan as FanIcon, Thermometer, Zap } from "lucide-react";
import { Card } from "../../components/Card";
import { TempRow } from "../../components/Readout";
import type { Device } from "../../types";

const ICONS = { board: CircuitBoard, cooler: Droplets, psu: Zap } as const;

export function BoardCard({ device, onOpen }: { device: Device; onOpen: (id: string) => void }) {
  const icon = ICONS[device.kind as keyof typeof ICONS] ?? Thermometer;
  return (
    <Card kind={device.kind} icon={icon} title={device.name} sub={device.sub} onOpen={() => onOpen(device.id)}>
      {device.temps.length > 0 ? (
        <ul className="m-0 p-0 list-none">{device.temps.map((t) => <TempRow key={t.id} t={t} />)}</ul>
      ) : (
        <p className="m-0 text-sm text-fg-muted">
          {device.kind === "board" && !device.id.startsWith("/apple")
            ? "沒有讀到主機板的溫度：桌機要 PawnIO 驅動＋系統管理員；筆電通常沒有這種感測器"
            : "沒有溫度感測器"}
        </p>
      )}
      {device.fans.length > 0 ? (
        <div className="m-fan">
          <h4 className="mt-4 mb-1 flex items-center gap-1.5 text-xs font-semibold text-(--m)">
            <FanIcon className="size-3.5" aria-hidden /> 風扇
          </h4>
          <ul className="m-0 p-0 list-none">
            {device.fans.map((f) => (
              <li key={f.id} className="flex items-baseline justify-between gap-3 py-1.5 border-t border-line first:border-t-0">
                <span className="text-sm text-fg-muted truncate">{f.label}</span>
                <span className="num text-sm">
                  <span className="text-(--m)">{f.value == null ? "—" : f.value.toLocaleString("zh-TW")}</span>
                  <span className="ml-1 text-xs text-fg-subtle">RPM</span>
                  {f.percent != null ? <span className="ml-2 text-xs text-fg-subtle">{f.percent}%</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {device.unplugged > 0 ? (
        <p className="mt-2 mb-0 text-xs text-fg-subtle">另有 {device.unplugged} 個風扇接頭沒接（或從沒轉過）</p>
      ) : null}
    </Card>
  );
}
