// 設定頁的「迷你模式」：背景不透明度、大小、鎖定，要顯示的項目（一組一行，箭頭調整上下順序）。
// 迷你模式是本體的疊加層視窗（後端 mini/），存檔後馬上照新的設定畫。FPS 讀不到時寫原因。
import { ArrowDown, ArrowUp } from "lucide-react";
import type { MiniGroup, MiniSettings } from "../../types";

const arrow =
  "grid place-items-center size-5 rounded text-fg-subtle hover:bg-surface-2 hover:text-fg cursor-pointer disabled:opacity-30 disabled:cursor-default disabled:hover:bg-transparent";

export function MiniSection({ value, defaults, catalog, range, fps, onChange }: {
  value: MiniSettings;
  defaults: MiniSettings;
  catalog: MiniGroup[];
  range: { opacity: [number, number]; scale: [number, number] };
  fps: string | null; // FPS 讀不到的原因
  onChange: (value: MiniSettings) => void;
}) {
  const groups = value.order
    .map((g) => catalog.find((c) => c.group === g))
    .filter((g): g is MiniGroup => g != null);

  function toggle(id: string) {
    const items = value.items.includes(id) ? value.items.filter((i) => i !== id) : [...value.items, id];
    onChange({ ...value, items });
  }

  function move(index: number, delta: -1 | 1) {
    const order = [...value.order];
    const other = index + delta;
    if (other < 0 || other >= order.length) return;
    [order[index], order[other]] = [order[other], order[index]];
    onChange({ ...value, order });
  }

  return (
    <section>
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="m-0 text-sm font-semibold">迷你模式</h3>
        <button type="button" onClick={() => onChange(defaults)}
          className="text-xs text-accent hover:underline cursor-pointer">恢復預設</button>
      </div>
      <p className="mt-0 mb-2.5 text-xs text-fg-subtle">
        疊在遊戲上的小視窗（像 RivaTuner）：拖曳移動、雙擊展開主畫面、右鍵有選單。存檔後馬上換。
      </p>
      <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-2.5 text-sm">
        <label htmlFor="mini-opacity" className="text-fg-muted">背景不透明度</label>
        <div className="flex items-center gap-3">
          <input id="mini-opacity" type="range" min={range.opacity[0]} max={range.opacity[1]} step={5}
            value={value.opacity} onChange={(e) => onChange({ ...value, opacity: Number(e.target.value) })}
            className="min-w-0 flex-1 accent-(--accent)" />
          <span className="w-12 shrink-0 text-right num">{value.opacity}%</span>
        </div>
        <label htmlFor="mini-scale" className="text-fg-muted">大小</label>
        <div className="flex items-center gap-3">
          <input id="mini-scale" type="range" min={range.scale[0]} max={range.scale[1]} step={10}
            value={value.scale} onChange={(e) => onChange({ ...value, scale: Number(e.target.value) })}
            className="min-w-0 flex-1 accent-(--accent)" />
          <span className="w-12 shrink-0 text-right num">{value.scale}%</span>
        </div>
        <span className="text-fg-muted">鎖定</span>
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={value.locked} onChange={(e) => onChange({ ...value, locked: e.target.checked })}
            className="size-4 shrink-0 accent-(--accent)" />
          滑鼠穿透，玩遊戲時點不到它（系統匣右鍵可以解除）
        </label>
      </div>

      <p className="mt-3.5 mb-1.5 text-xs text-fg-subtle">要顯示的項目：一組一行，箭頭調整上下順序</p>
      <ul className="m-0 p-0 list-none rounded-xl border border-line divide-y divide-line">
        {groups.map((g, i) => (
          <li key={g.group} className="flex items-start gap-2 px-2.5 py-2">
            <span className="flex shrink-0 gap-0.5">
              <button type="button" className={arrow} disabled={i === 0} onClick={() => move(i, -1)}
                aria-label={`${g.label}往上移`} title="往上移">
                <ArrowUp className="size-3.5" aria-hidden />
              </button>
              <button type="button" className={arrow} disabled={i === groups.length - 1} onClick={() => move(i, 1)}
                aria-label={`${g.label}往下移`} title="往下移">
                <ArrowDown className="size-3.5" aria-hidden />
              </button>
            </span>
            <span className="w-14 shrink-0 text-sm font-medium">{g.label}</span>
            <span className="flex min-w-0 flex-wrap gap-x-3.5 gap-y-1.5">
              {g.items.map((item) => (
                <label key={item.id} className="flex items-center gap-1.5 text-sm cursor-pointer">
                  <input type="checkbox" checked={value.items.includes(item.id)} onChange={() => toggle(item.id)}
                    className="size-4 shrink-0 accent-(--accent)" />
                  {item.label}
                </label>
              ))}
              {g.group === "fps" && fps ? <span className="basis-full text-xs text-warm">{fps}</span> : null}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 mb-0 text-xs text-fg-subtle">
        FPS 用 Intel 的 PresentMon 讀（不碰遊戲本身，不會被反作弊誤判），只有迷你模式開著、有勾 FPS 時才跑。
        獨佔全螢幕的遊戲會蓋過迷你模式，遊戲改成「無邊框視窗」就看得到。
      </p>
    </section>
  );
}
