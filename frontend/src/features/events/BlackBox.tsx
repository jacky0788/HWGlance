// 黑盒子：點開一件事時，看出事前後的讀數（後端 src/hwglance/sensors/blackbox.py，GET /api/blackbox）。
// 當機的話，「記錄停在幾點」差不多就是當機的時間（Windows 記的最後運作時間常常早半小時以上）。
// 先列最相關的幾項（牽涉到的硬碟、CPU／GPU 溫度、功耗、負載），其他的收在「全部讀數」。
import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { day, hms, number, temp } from "../../lib/format";
import { useUnit } from "../../lib/prefs";
import type { BlackBoxKey, BlackBoxWindow, Incident, Unit } from "../../types";

const BEFORE = 300; // 出事前 5 分鐘
const AFTER = 60; // 不是當機的：出事後再看 1 分鐘
const SPAN = 6 * 3600; // 後端一次最多 6 小時
const STOPPED = 30; // 當機：斷了超過 30 秒才算「記錄停了」
const SHOWN = 6;
const MIN_SPAN: Record<string, number> = { "°C": 10, W: 50, "%": 20 }; // 上下範圍至少這麼多，雜訊才不會被放大

function windowOf(i: Incident): [number, number] {
  const from = i.start - BEFORE;
  const to = i.category === "crash" ? i.end : i.end + AFTER;
  return [from, Math.min(to, from + SPAN)];
}

function pick(keys: BlackBoxKey[], i: Incident): BlackBoxKey[] {
  const involved = (k: BlackBoxKey) => i.disks.some((n) => k.device.endsWith(`/${n}`));
  const rules: ((k: BlackBoxKey) => boolean)[] = [
    (k) => k.kind === "storage" && k.unit === "°C" && involved(k),
    (k) => k.kind === "cpu" && k.unit === "°C",
    (k) => k.kind === "gpu" && k.role === "gpu",
    (k) => k.kind === "gpu" && k.role === "power",
    (k) => k.kind === "cpu" && k.role === "power",
    (k) => k.kind === "gpu" && k.role === "load",
    (k) => k.kind === "cpu" && k.role === "load",
  ];
  const out: BlackBoxKey[] = [];
  for (const rule of rules) {
    for (const k of keys) {
      if (rule(k) && !out.includes(k)) out.push(k);
    }
  }
  return out.slice(0, SHOWN);
}

// 當機：第一段斷掉的地方（或範圍結束前就沒了）＝記錄停在幾點
function stoppedAt(w: BlackBoxWindow, i: Incident): number | null {
  if (i.category !== "crash") return null;
  const gap = w.gaps.find(([a, b]) => b - a >= STOPPED);
  if (gap) return gap[0];
  return w.last != null && w.to - w.last >= STOPPED ? w.last : null;
}

function show(k: BlackBoxKey, v: number | null | undefined, unit: Unit): string {
  if (k.unit === "°C") return temp(v, unit);
  return v == null ? "—" : `${number(Math.round(v))} ${k.unit}`;
}

function Chart({ k, w, mark, stop, unit }: {
  k: BlackBoxKey;
  w: BlackBoxWindow;
  mark: number; // 出事的時間：灰色虛線
  stop: number | null; // 當機時記錄停的地方：紅線
  unit: Unit;
}) {
  const values = w.series[k.key] ?? [];
  const nums = values.filter((v): v is number => v != null);
  let lo = Math.min(...nums);
  let hi = Math.max(...nums);
  const min = MIN_SPAN[k.unit] ?? 10;
  if (hi - lo < min) {
    const mid = (hi + lo) / 2;
    lo = mid - min / 2;
    hi = mid + min / 2;
  }
  const x = (t: number) => ((t - w.from) / (w.to - w.from)) * 100;
  let d = "";
  let pen = false;
  values.forEach((v, n) => {
    if (v == null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(w.t[n]).toFixed(2)} ${(22 - ((v - lo) / (hi - lo)) * 20).toFixed(2)}`;
    pen = true;
  });
  const at = x(mark);
  const end = stop != null ? x(stop) : null;
  return (
    <figure className={`kind-${k.kind} level-${k.level ?? "ok"} m-0 min-w-0`}>
      <figcaption className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-fg-muted truncate" title={k.label}>{k.label}</span>
        <span className="num whitespace-nowrap text-fg-subtle">
          最高 <span className={`font-semibold ${k.level ? "text-(--level)" : "text-fg"}`}>{show(k, k.max, unit)}</span>
        </span>
      </figcaption>
      <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="mt-1 block w-full h-10 rounded bg-surface-2" role="img"
        aria-label={`${k.label}：最高 ${show(k, k.max, unit)}`}>
        {at > 0 && at < 100 ? (
          <line x1={at} x2={at} y1={0} y2={24} stroke="var(--fg-subtle)" strokeWidth={1} strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke" />
        ) : null}
        {end != null && end > 0 && end < 100 ? (
          <line x1={end} x2={end} y1={0} y2={24} stroke="var(--hot)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        ) : null}
        {nums.length > 0 ? (
          <path d={d} fill="none" stroke="var(--deco)" strokeWidth={1.5} vectorEffect="non-scaling-stroke"
            strokeLinejoin="round" strokeLinecap="round" />
        ) : null}
      </svg>
    </figure>
  );
}

// 一句話：溫度有沒有到偏高／過熱（排除「是不是太熱」最常被問）
function Verdict({ w, unit }: { w: BlackBoxWindow; unit: Unit }) {
  const temps = w.keys.filter((k) => k.unit === "°C" && k.max != null);
  const list = (ks: BlackBoxKey[]) => ks.slice(0, 3).map((k) => `${k.label} ${show(k, k.max, unit)}`).join("、");
  const hot = temps.filter((k) => k.level === "hot");
  const warm = temps.filter((k) => k.level === "warm");
  const main = temps.filter((k) => (k.kind === "cpu" || k.kind === "gpu") && (k.role === "cpu" || k.role === "gpu"));
  const [level, text] = hot.length > 0
    ? ["hot", `有溫度過熱：${list(hot)}`]
    : warm.length > 0
      ? ["warm", `溫度偏高但沒到過熱：${list(warm)}`]
      : ["ok", `溫度都在正常範圍${main.length > 0 ? `（${list(main)}）` : ""}`];
  return <p className={`level-${level} m-0 text-sm`}><span className="text-(--level)">●</span> {text}</p>;
}

export function BlackBox({ incident, now }: { incident: Incident; now: number }) {
  const unit = useUnit();
  const [w, setW] = useState<BlackBoxWindow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [from, to] = windowOf(incident);

  useEffect(() => {
    let cancelled = false;
    api.blackbox(from, to)
      .then((data) => {
        if (!cancelled) setW(data);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [from, to]);

  const title = <h4 className="m-0 mb-1 text-xs font-semibold text-fg-muted">出事前後的讀數（黑盒子）</h4>;
  if (w == null) {
    return <section>{title}<p className="m-0 text-xs text-fg-subtle">{error ? `讀不到黑盒子：${error}` : "讀取中…"}</p></section>;
  }
  if (w.count === 0 || w.first == null || w.last == null) {
    const old = now - incident.end > w.keepDays * 86400;
    return (
      <section>
        {title}
        <p className="m-0 text-xs text-fg-subtle">
          {old ? `黑盒子只留最近 ${w.keepDays} 天，這件事已經超過了。` : "那段時間沒有黑盒子的紀錄（HWGlance 要開著才會記）。"}
        </p>
      </section>
    );
  }
  const picked = pick(w.keys, incident);
  const rest = w.keys.filter((k) => !picked.includes(k));
  const stop = stoppedAt(w, incident);
  const at = (t: number) => `${day(t, now)} ${hms(t)}`;
  return (
    <section className="space-y-2">
      {title}
      <p className="m-0 text-xs text-fg-subtle num">
        {at(w.first)}～{hms(w.last)}，{w.count > w.t.length ? `每 ${w.step} 秒取最大值` : `每 ${w.step} 秒一筆`}；
        灰色虛線是出事的時間{stop != null ? "，紅線是記錄停的地方" : ""}。
      </p>
      {stop != null ? (
        <p className="level-hot m-0 text-sm">
          <span className="text-(--level)">●</span> 記錄停在 <span className="num font-semibold">{at(stop)}</span>
          ，之後就沒有了：當機大概就在這時候。
        </p>
      ) : null}
      <Verdict w={w} unit={unit} />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,11rem),1fr))] gap-x-4 gap-y-3">
        {(all ? [...picked, ...rest] : picked).map((k) => (
          <Chart key={k.key} k={k} w={w} mark={incident.start} stop={stop} unit={unit} />
        ))}
      </div>
      {rest.length > 0 ? (
        <button type="button" aria-expanded={all} onClick={() => setAll((v) => !v)}
          className="inline-flex items-center gap-0.5 text-xs text-accent hover:underline cursor-pointer">
          {all ? "只看主要的" : `全部 ${w.keys.length} 項讀數`}
          <ChevronRight className={`size-3.5 transition-transform ${all ? "rotate-90" : ""}`} aria-hidden />
        </button>
      ) : null}
    </section>
  );
}
