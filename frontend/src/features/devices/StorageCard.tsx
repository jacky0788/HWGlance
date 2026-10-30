// 所有硬碟放在同一張卡：每顆一列（溫度、類型、已用比例）。每一列都是按鈕，點了打開那顆硬碟的詳細資訊。
import { HardDrive } from "lucide-react";
import { Card } from "../../components/Card";
import { size, temp, tempExact } from "../../lib/format";
import { useUnit } from "../../lib/prefs";
import type { Device } from "../../types";

function DriveRow({ device, onOpen }: { device: Device; onOpen: (id: string) => void }) {
  const unit = useUnit();
  const head = device.temps.find((t) => t.id === device.headline) ?? null;
  const used = device.stats.find((s) => s.key === "used");
  const free = device.stats.find((s) => s.key === "free");
  return (
    <li className="border-t border-line first:border-t-0">
      <button type="button" onClick={() => onOpen(device.id)} aria-label={`${device.name}：詳細資訊`}
        className={`level-${head?.level ?? "none"} block w-[calc(100%+1rem)] -mx-2 px-2 py-2 rounded-lg text-left cursor-pointer hover:bg-surface-2 transition-colors`}>
        <span className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 flex items-baseline gap-2">
            <span className="text-sm truncate" title={device.name}>{device.name}</span>
            <span className="shrink-0 text-xs text-fg-subtle">{device.sub}</span>
          </span>
          <span className="num text-sm shrink-0" title={head ? `最高 ${tempExact(head.max, unit)}` : undefined}>
            <span className="font-semibold text-(--level)">{temp(head?.value, unit)}</span>
          </span>
        </span>
        {used ? (
          <span className="mt-1.5 flex items-center gap-2">
            <span className="h-1.5 flex-1 rounded-full bg-track overflow-hidden">
              <span className="block h-full rounded-full bg-fg-subtle" style={{ width: `${Math.min(100, used.value)}%` }} />
            </span>
            <span className="text-xs text-fg-subtle num shrink-0">
              已用 {used.value}%{free ? `・剩 ${size(free.value)}` : ""}
            </span>
          </span>
        ) : null}
      </button>
    </li>
  );
}

export function StorageCard({ drives, onOpen }: { drives: Device[]; onOpen: (id: string) => void }) {
  return (
    <Card kind="storage" icon={HardDrive} title="硬碟" sub={`${drives.length} 顆・點一顆看詳細`}>
      <ul className="m-0 p-0 list-none">
        {drives.map((d) => <DriveRow key={d.id} device={d} onOpen={onOpen} />)}
      </ul>
    </Card>
  );
}
