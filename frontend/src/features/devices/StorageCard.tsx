// 所有硬碟放在同一張卡：每顆一列（溫度、類型、已用比例）。每列有自己的 id，點總覽磚塊會捲到那一列。
import { HardDrive } from "lucide-react";
import { anchorId, Card } from "../../components/Card";
import { size, temp, tempExact } from "../../lib/format";
import { useUnit } from "../../lib/prefs";
import type { Device } from "../../types";

function DriveRow({ device }: { device: Device }) {
  const unit = useUnit();
  const head = device.temps.find((t) => t.id === device.headline) ?? null;
  const used = device.stats.find((s) => s.key === "used");
  const free = device.stats.find((s) => s.key === "free");
  return (
    <li id={anchorId(device.id)} className={`level-${head?.level ?? "none"} rounded-lg px-2 -mx-2 py-2 border-t border-line first:border-t-0`}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 flex items-baseline gap-2">
          <span className="text-sm truncate" title={device.name}>{device.name}</span>
          <span className="shrink-0 text-xs text-fg-subtle">{device.sub}</span>
        </span>
        <span className="num text-sm shrink-0" title={head ? `最高 ${tempExact(head.max, unit)}` : undefined}>
          <span className="font-semibold text-(--level)">{temp(head?.value, unit)}</span>
        </span>
      </div>
      {used ? (
        <div className="mt-1.5 flex items-center gap-2">
          <div className="h-1.5 flex-1 rounded-full bg-track overflow-hidden">
            <div className="h-full rounded-full bg-fg-subtle" style={{ width: `${Math.min(100, used.value)}%` }} />
          </div>
          <span className="text-xs text-fg-subtle num shrink-0">
            已用 {used.value}%{free ? `・剩 ${size(free.value)}` : ""}
          </span>
        </div>
      ) : null}
    </li>
  );
}

export function StorageCard({ drives }: { drives: Device[] }) {
  return (
    <Card icon={HardDrive} title="硬碟" sub={`${drives.length} 顆`}>
      <ul className="m-0 p-0 list-none">
        {drives.map((d) => <DriveRow key={d.id} device={d} />)}
      </ul>
    </Card>
  );
}
