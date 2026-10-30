// CPU、GPU 的大卡：左邊弧形儀表（主溫度），右邊負載、時脈、功耗、風扇與其他溫度，底下近 5 分鐘走勢。
import { Cpu, Gpu } from "lucide-react";
import { Card } from "../../components/Card";
import { StatReadout, TempReadout } from "../../components/Readout";
import { Sparkline } from "../../components/Sparkline";
import { TempGauge } from "../../components/TempGauge";
import type { Series } from "../../lib/useHistory";
import type { Device } from "../../types";

export function ChipCard({ device, series, onOpen }: { device: Device; series: Series; onOpen: (id: string) => void }) {
  const head = device.temps.find((t) => t.id === device.headline) ?? null;
  const others = device.temps.filter((t) => t !== head);
  const isCpu = device.kind === "cpu";
  return (
    <Card kind={device.kind} icon={isCpu ? Cpu : Gpu} title={device.name} onOpen={() => onOpen(device.id)}>
      <div className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] gap-4 items-center">
        <TempGauge value={head?.value ?? null} level={head?.level ?? "none"} label={isCpu ? "CPU" : "GPU"} />
        <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-2">
          {device.stats.map((s) => <StatReadout key={s.key} stat={s} kind={device.kind} />)}
          {others.map((t) => <TempReadout key={t.id} t={t} />)}
        </dl>
      </div>
      {head ? (
        <div className="mt-3">
          <Sparkline values={series[head.id] ?? []} level={head.level} label={`${head.label}・近 5 分鐘`} />
        </div>
      ) : (
        <p className="mt-3 mb-0 text-xs text-fg-subtle">
          讀不到溫度：{isCpu ? "需要 PawnIO 驅動＋系統管理員" : "顯示卡驅動沒有回報"}
        </p>
      )}
    </Card>
  );
}
