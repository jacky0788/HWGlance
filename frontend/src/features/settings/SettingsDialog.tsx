// 設定：溫度單位、更新頻率、關掉視窗後要不要留在系統匣、各種硬體的溫度門檻、卡片顏色。
// 門檻一律用 °C 存（後端 settings.py）；選 °F 時只是顯示與輸入換算。存檔後下一輪讀數就生效。
// 顏色點了馬上預覽（寫到 <html> 上），按取消或關掉就換回存過的。
import { useEffect, useRef, useState } from "react";
import { Segmented } from "../../components/Segmented";
import { api, errorMessage } from "../../lib/api";
import { PRESET_NAMES, applyKindColors, changedColors, swatchHex } from "../../lib/colors";
import { fromUnit, toUnit } from "../../lib/format";
import type { SettingsPayload, Unit } from "../../types";

type Bands = Record<string, [number, number]>;

function round1(v: number): number {
  return Math.round(v * 10) / 10;
}

const SELECTED = "ring-2 ring-fg ring-offset-2 ring-offset-surface";

function ColorPicker({ name, value, fallback, presets, onChange }: {
  name: string; // CPU、顯示卡…
  value: string; // 調色盤名稱或 #rrggbb
  fallback: string; // 預設
  presets: string[];
  onChange: (value: string) => void;
}) {
  const custom = value.startsWith("#");
  return (
    <>
      <span className="text-fg-muted">{name}</span>
      <div role="radiogroup" aria-label={`${name}的顏色`} className="flex flex-wrap items-center gap-2">
        {presets.map((p) => (
          <button key={p} type="button" role="radio" aria-checked={value === p} onClick={() => onChange(p)}
            aria-label={PRESET_NAMES[p] ?? p} title={PRESET_NAMES[p] ?? p}
            className={`size-6 rounded-full cursor-pointer ${value === p ? SELECTED : "hover:scale-110 transition-transform"}`}
            style={{ background: `var(--swatch-${p})` }} />
        ))}
        <label title="自訂顏色：深色、淺色模式會自動調亮或調暗，才看得清楚"
          className={`flex items-center gap-1.5 rounded-full border pl-0.5 pr-2 py-0.5 text-xs cursor-pointer ${custom ? "border-fg text-fg" : "border-line text-fg-muted hover:border-line-strong"}`}>
          <input type="color" className="swatch-input" aria-label={`${name}的自訂顏色`}
            value={custom ? value : swatchHex(value)} onChange={(e) => onChange(e.target.value)} />
          自訂
        </label>
        {value !== fallback ? (
          <button type="button" onClick={() => onChange(fallback)}
            className="text-xs text-fg-subtle hover:text-fg cursor-pointer">恢復預設</button>
        ) : null}
      </div>
    </>
  );
}

export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [data, setData] = useState<SettingsPayload | null>(null);
  const [unit, setUnit] = useState<Unit>("C");
  const [refresh, setRefresh] = useState(1);
  const [closeToTray, setCloseToTray] = useState(true);
  const [bands, setBands] = useState<Bands>({});
  const [colors, setColors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [quitting, setQuitting] = useState(false);
  const committed = useRef<Record<string, string> | null>(null); // 存過的顏色（只有改過的），關掉時換回來

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    if (open && data) applyKindColors(changedColors(colors, data.colorDefaults));
  }, [open, data, colors]);

  useEffect(() => {
    if (!open && committed.current) {
      applyKindColors(committed.current);
      committed.current = null;
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError(null);
    api
      .settings()
      .then((s) => {
        if (cancelled) return;
        committed.current = changedColors(s.colors, s.colorDefaults);
        setData(s);
        setUnit(s.unit);
        setRefresh(s.interval);
        setCloseToTray(s.closeToTray);
        setBands(s.bands);
        setColors(s.colors);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const kinds = data ? Object.keys(data.labels) : [];
  const invalid = data
    ? kinds.filter((k) => {
        const [warm, hot] = bands[k] ?? [0, 0];
        return !(data.range[0] <= warm && warm < hot && hot <= data.range[1]);
      })
    : [];

  function setBand(kind: string, index: 0 | 1, shown: string) {
    const n = Number(shown);
    if (!Number.isFinite(n) || shown.trim() === "") return;
    setBands((prev) => {
      const pair: [number, number] = [...(prev[kind] ?? [0, 0])] as [number, number];
      pair[index] = round1(fromUnit(n, unit));
      return { ...prev, [kind]: pair };
    });
  }

  async function save() {
    if (!data) return;
    if (invalid.length > 0) {
      setError(`這些門檻不合理（偏高要小於過熱）：${invalid.map((k) => data.labels[k]).join("、")}`);
      return;
    }
    setSaving(true);
    try {
      await api.saveSettings({ unit, interval: refresh, closeToTray, bands, colors });
      committed.current = changedColors(colors, data.colorDefaults);
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function quit() {
    setQuitting(true);
    try {
      await api.quit();
    } catch (e) {
      setError(errorMessage(e));
      setQuitting(false);
    }
  }

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="settings-title"
      className="m-auto w-[min(42rem,94vw)] max-h-[90vh] rounded-2xl border border-line bg-surface text-fg p-0 backdrop:bg-black/50">
      <div className="flex max-h-[90vh] flex-col">
        <header className="px-5 pt-4 pb-3 border-b border-line">
          <h2 id="settings-title" className="m-0 text-base font-semibold">設定</h2>
        </header>
        {data == null ? (
          <p className="m-0 px-5 py-8 text-sm text-fg-muted">{error ?? "讀取中…"}</p>
        ) : (
          <div className="overflow-y-auto px-5 py-4 space-y-5">
            <section className="grid grid-cols-[auto_1fr] items-center gap-x-6 gap-y-3 text-sm">
              <span className="text-fg-muted">溫度單位</span>
              <Segmented label="溫度單位" value={unit} onChange={setUnit}
                options={[{ value: "C", text: "°C" }, { value: "F", text: "°F" }]} />
              <span className="text-fg-muted">更新頻率</span>
              <Segmented label="更新頻率" value={refresh} onChange={setRefresh}
                options={data.intervals.map((s) => ({ value: s, text: `${s} 秒` }))} />
              <span className="text-fg-muted">關掉視窗時</span>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={closeToTray} onChange={(e) => setCloseToTray(e.target.checked)}
                  className="size-4 accent-(--accent)" />
                留在系統匣繼續監控（圖示上的數字就是 CPU 溫度）
              </label>
            </section>

            <section>
              <div className="flex items-baseline justify-between mb-2">
                <h3 className="m-0 text-sm font-semibold">溫度門檻</h3>
                <button type="button" onClick={() => setBands(data.defaults)}
                  className="text-xs text-accent hover:underline cursor-pointer">恢復預設</button>
              </div>
              <p className="mt-0 mb-2 text-xs text-fg-subtle">到「偏高」變黃、到「過熱」變紅。單位 °{unit}。</p>
              <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-3 gap-y-1.5 items-center text-sm">
                <span className="text-xs text-fg-subtle">種類</span>
                <span className="text-xs text-warm w-[4.5rem]">偏高</span>
                <span className="text-xs text-hot w-[4.5rem]">過熱</span>
                {kinds.map((k) => {
                  const [warm, hot] = bands[k] ?? [0, 0];
                  const bad = invalid.includes(k);
                  const cls = `w-[4.5rem] rounded-md border bg-surface-2 px-2 py-1 num text-right ${bad ? "border-hot" : "border-line"}`;
                  return (
                    <div key={k} className="contents">
                      <span className="truncate text-fg-muted">{data.labels[k]}</span>
                      <input type="number" step={1} aria-label={`${data.labels[k]} 偏高`} className={cls}
                        value={Math.round(toUnit(warm, unit))} onChange={(e) => setBand(k, 0, e.target.value)} />
                      <input type="number" step={1} aria-label={`${data.labels[k]} 過熱`} className={cls}
                        value={Math.round(toUnit(hot, unit))} onChange={(e) => setBand(k, 1, e.target.value)} />
                    </div>
                  );
                })}
              </div>
            </section>

            <section>
              <div className="flex items-baseline justify-between mb-2">
                <h3 className="m-0 text-sm font-semibold">顏色</h3>
                <button type="button" onClick={() => setColors(data.colorDefaults)}
                  className="text-xs text-accent hover:underline cursor-pointer">全部恢復預設</button>
              </div>
              <p className="mt-0 mb-2.5 text-xs text-fg-subtle">
                {"每種硬體的圖示、色條、儀表、走勢用這個顏色，點了馬上看得到。"}
                {"溫度數字照舊是綠、黃、紅，自訂時避開黃、紅，才不會跟「偏高」「過熱」搞混。"}
              </p>
              <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-2.5 text-sm">
                {Object.entries(data.colorLabels).map(([kind, name]) => (
                  <ColorPicker key={kind} name={name} value={colors[kind] ?? data.colorDefaults[kind]}
                    fallback={data.colorDefaults[kind]} presets={data.presets}
                    onChange={(v) => setColors((prev) => ({ ...prev, [kind]: v }))} />
                ))}
              </div>
            </section>
            {error ? <p role="alert" className="m-0 text-sm text-hot">{error}</p> : null}
          </div>
        )}
        <footer className="flex items-center gap-2 px-5 py-3 border-t border-line">
          <button type="button" onClick={() => void quit()} disabled={quitting}
            className="rounded-lg px-3 py-1.5 text-sm text-hot hover:bg-hot-soft cursor-pointer">
            {quitting ? "結束中…" : "結束 HWGlance"}
          </button>
          <span className="flex-1" />
          <button type="button" onClick={onClose}
            className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-line-strong cursor-pointer">取消</button>
          <button type="button" onClick={() => void save()} disabled={data == null || saving}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-semibold text-page hover:opacity-90 cursor-pointer">
            {saving ? "儲存中…" : "儲存"}
          </button>
        </footer>
      </div>
    </dialog>
  );
}
