// 溫度總覽：每個重要的溫度一塊磚，三色分級；「一眼看出所有硬體的溫度」就是這一排。
import type { Tile } from "../../types";
import { temp, tempExact } from "../../lib/format";
import { useUnit } from "../../lib/prefs";

function Summary({ tiles }: { tiles: Tile[] }) {
  const hot = tiles.filter((t) => t.level === "hot").length;
  const warm = tiles.filter((t) => t.level === "warm").length;
  if (hot > 0) return <span className="text-sm font-semibold text-hot">{hot} 個過熱</span>;
  if (warm > 0) return <span className="text-sm font-semibold text-warm">{warm} 個偏高</span>;
  return <span className="text-sm font-semibold text-ok">全部正常</span>;
}

function TempTile({ tile, onPick }: { tile: Tile; onPick: (deviceId: string) => void }) {
  const unit = useUnit();
  return (
    <button type="button" onClick={() => onPick(tile.device)}
      title={`${tile.label}（${tile.sub}）：${tempExact(tile.value, unit)}，開啟以來最高 ${tempExact(tile.max, unit)}`}
      className={`kind-${tile.kind} kind-frame level-${tile.level} relative min-w-0 text-left rounded-xl border bg-surface pl-3.5 pr-3 py-2.5 cursor-pointer transition-colors`}>
      <span aria-hidden className="absolute left-0 top-3 bottom-3 w-1 rounded-r-full bg-(--level)" />
      <span className="block text-xs text-fg-muted truncate">{tile.label}</span>
      <span className="flex items-baseline justify-between gap-2">
        <span className="text-2xl font-semibold num leading-tight text-(--level)">{temp(tile.value, unit)}</span>
        <span className="text-xs text-fg-subtle num shrink-0">最高 {temp(tile.max, unit)}</span>
      </span>
      {/* 小字（型號）自己一行，才不會被「最高」擠到看不完 */}
      <span className="block text-xs text-fg-subtle truncate">{tile.sub}</span>
    </button>
  );
}

export function Overview({ tiles, onPick }: { tiles: Tile[]; onPick: (deviceId: string) => void }) {
  return (
    <section aria-labelledby="overview-title">
      <div className="flex items-baseline gap-3 mb-2">
        <h2 id="overview-title" className="m-0 text-sm font-semibold text-fg-muted">溫度總覽</h2>
        {tiles.length > 0 ? <Summary tiles={tiles} /> : null}
        <span className="ml-auto text-xs text-fg-subtle">{tiles.length} 個溫度・點一下看詳細</span>
      </div>
      {tiles.length > 0 ? (
        <div className="tiles">
          {tiles.map((t) => <TempTile key={t.id} tile={t} onPick={onPick} />)}
        </div>
      ) : (
        <p className="m-0 rounded-xl border border-dashed border-line-strong px-4 py-6 text-sm text-fg-muted text-center">
          還沒有讀到任何溫度
        </p>
      )}
    </section>
  );
}
