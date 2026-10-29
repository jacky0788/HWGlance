// 主畫面：總覽 → CPU／GPU 大卡 → 主機板、記憶體、硬碟。只負責排版，內容在 features/。
import { anchorId } from "../components/Card";
import { BoardCard } from "../features/devices/BoardCard";
import { ChipCard } from "../features/devices/ChipCard";
import { MemoryCard } from "../features/devices/MemoryCard";
import { StorageCard } from "../features/devices/StorageCard";
import { Overview } from "../features/overview/Overview";
import { Footer, Notes } from "../features/status/StatusBar";
import type { Series } from "../lib/useHistory";
import type { Snapshot } from "../types";

function flashDevice(deviceId: string) {
  const el = document.getElementById(anchorId(deviceId));
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // 重新觸發動畫
  el.classList.add("flash");
}

export function Dashboard({ snap, series, offline }: { snap: Snapshot; series: Series; offline: string | null }) {
  const of = (kind: string) => snap.devices.filter((d) => d.kind === kind);
  const chips = [...of("cpu"), ...of("gpu")];
  const side = [...of("board"), ...of("cooler"), ...of("psu"), ...of("other")];
  const memory = of("memory");
  const drives = of("storage");
  return (
    <>
      <main className="flex-1 px-5 py-4 space-y-4">
        <Notes notes={snap.source.notes} offline={offline} />
        <Overview tiles={snap.overview} onPick={flashDevice} />
        {chips.length > 0 ? (
          <div className="cards-2">
            {chips.map((d) => <ChipCard key={d.id} device={d} series={series} />)}
          </div>
        ) : null}
        <div className="cards-3">
          {side.length > 0 ? (
            <div className="space-y-3 min-w-0">{side.map((d) => <BoardCard key={d.id} device={d} />)}</div>
          ) : null}
          {memory.length > 0 ? (
            <div className="space-y-3 min-w-0">{memory.map((d) => <MemoryCard key={d.id} device={d} />)}</div>
          ) : null}
          {drives.length > 0 ? <StorageCard drives={drives} /> : null}
        </div>
      </main>
      <Footer snap={snap} />
    </>
  );
}
