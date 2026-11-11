// 主畫面：總覽 → CPU／GPU 大卡 → 主機板、記憶體、硬碟。只負責排版，內容在 features/。
// 點總覽磚塊、卡片或硬碟那一列，都會打開那個硬體的詳細資訊（features/details/）。
import { useState } from "react";
import { BoardCard } from "../features/devices/BoardCard";
import { ChipCard } from "../features/devices/ChipCard";
import { MemoryCard } from "../features/devices/MemoryCard";
import { StorageCard } from "../features/devices/StorageCard";
import { DetailDialog } from "../features/details/DetailDialog";
import { Overview } from "../features/overview/Overview";
import { Footer, Notes } from "../features/status/StatusBar";
import { hasFeature } from "../lib/features";
import type { Series } from "../lib/useHistory";
import type { Snapshot } from "../types";

export function Dashboard({ snap, series, offline }: { snap: Snapshot; series: Series; offline: string | null }) {
  const [detailId, setDetailId] = useState<string | null>(null);
  const of = (kind: string) => snap.devices.filter((d) => d.kind === kind);
  const chips = [...of("cpu"), ...of("gpu")];
  const side = [...of("board"), ...of("cooler"), ...of("psu"), ...of("other")];
  const memory = of("memory");
  const drives = of("storage");
  return (
    <>
      <main className="flex-1 px-5 py-4 space-y-4">
        <Notes notes={snap.source.notes} offline={offline} />
        <Overview tiles={snap.overview} onPick={setDetailId} />
        {chips.length > 0 ? (
          <div className="cards-2">
            {chips.map((d) => <ChipCard key={d.id} device={d} series={series} onOpen={setDetailId} />)}
          </div>
        ) : null}
        <div className="cards-3">
          {side.length > 0 ? (
            <div className="space-y-3 min-w-0">{side.map((d) => <BoardCard key={d.id} device={d} onOpen={setDetailId} />)}</div>
          ) : null}
          {memory.length > 0 ? (
            <div className="space-y-3 min-w-0">
              {memory.map((d) => (
                <MemoryCard key={d.id} device={d} breakdown={hasFeature(snap, "memory")}
                  unified={snap.devices.some((x) => x.id.startsWith("/apple"))} onOpen={setDetailId} />
              ))}
            </div>
          ) : null}
          {drives.length > 0 ? <StorageCard drives={drives} onOpen={setDetailId} /> : null}
        </div>
      </main>
      <Footer snap={snap} />
      <DetailDialog deviceId={detailId} device={snap.devices.find((d) => d.id === detailId) ?? null}
        interval={snap.interval} onClose={() => setDetailId(null)} />
    </>
  );
}
