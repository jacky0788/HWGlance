// 事件頁上方：這段期間藍屏、意外重開、顯示卡、硬碟、CPU／記憶體、程式當掉各幾次，下面一排篩選。
// 數字照嚴重程度上色（有藍屏是紅色；沒有就寫綠色的「沒有」），圖示用硬體種類色，外框維持灰色。
import { AppWindow, Cpu, Gpu, HardDrive, MonitorX, RotateCcw } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { number, when } from "../../lib/format";
import type { EventCounts, EventReport, Incident } from "../../types";
import { LABELS, isMac, matches } from "./meta";
import type { Filter } from "./meta";

interface CardDef {
  label: string;
  unit: string;
  icon: LucideIcon;
  tone: string;
  title: string;
  count: (c: EventCounts) => number;
  match: (i: Incident) => boolean;
  severe: "always" | "major" | "never"; // 有的時候是紅色（always）、有要注意的才紅（major）、只到黃色（never）
  note?: (c: EventCounts) => string | null; // 小字；沒有就寫最近一次的時間
}

const CARDS: CardDef[] = [
  { label: "藍屏", unit: "次", icon: MonitorX, tone: "text-fg-subtle", severe: "always",
    title: "Windows 遇到嚴重錯誤，留下藍屏代碼後重新開機", count: (c) => c.bluescreen, match: (i) => i.badge === "藍屏" },
  { label: "意外重開", unit: "次", icon: RotateCcw, tone: "text-fg-subtle", severe: "always",
    title: "沒有藍屏代碼的重開：斷電、強制關機、睡眠恢復失敗、過熱關機", count: (c) => c.reset,
    match: (i) => i.category === "crash" && i.badge !== "藍屏" },
  { label: "顯示卡出錯", unit: "件", icon: Gpu, tone: "kind-gpu text-(--kind)", severe: "major",
    title: "顯示卡驅動記下的錯誤、TDR 重設、即時傾印（含藍屏前的）", count: (c) => c.gpu, match: (i) => i.involves.includes("gpu"),
    note: (c) => (c.gpuMajor > 0 ? `其中 ${c.gpuMajor} 件要注意` : null) },
  { label: "硬碟出錯", unit: "件", icon: HardDrive, tone: "kind-storage text-(--kind)", severe: "major",
    title: "硬碟斷線、壞軌、讀寫錯誤、控制器重設", count: (c) => c.storage, match: (i) => i.involves.includes("storage") },
  { label: "CPU／記憶體", unit: "件", icon: Cpu, tone: "kind-cpu text-(--kind)", severe: "major",
    title: "WHEA 硬體錯誤（CPU、記憶體、PCIe）、CPU 被韌體限速", count: (c) => c.hardware,
    match: (i) => i.involves.includes("hardware") },
  { label: "程式當掉", unit: "次", icon: AppWindow, tone: "kind-other text-(--kind)", severe: "never",
    title: "程式當掉或沒有回應的次數", count: (c) => c.app, match: (i) => i.involves.includes("app") },
];

// Mac 只記得到核心錯誤與不正常的關機（events/mac.py）：顯示卡、硬碟、程式當掉這幾格放了只會一直寫「沒有」
const MAC_CARDS: CardDef[] = [
  { label: "核心錯誤", unit: "次", icon: MonitorX, tone: "text-fg-subtle", severe: "always",
    title: "macOS 的核心遇到沒辦法繼續的錯誤（kernel panic），留下報告後重新開機", count: (c) => c.bluescreen,
    match: (i) => i.badge === "核心錯誤" },
  { label: "意外重開", unit: "次", icon: RotateCcw, tone: "text-fg-subtle", severe: "always",
    title: "沒有核心錯誤報告的不正常關機：系統卡住被看門狗重開、按住電源鍵、斷電、過熱", count: (c) => c.reset,
    match: (i) => i.category === "crash" && i.badge !== "核心錯誤" },
];

function StatCard({ def, report }: { def: CardDef; report: EventReport }) {
  const n = def.count(report.counts);
  const hits = report.incidents.filter(def.match); // 新的在前
  const severe = def.severe === "always" || (def.severe === "major" && hits.some((i) => i.major));
  const level = n === 0 ? "level-ok" : severe ? "level-hot" : "level-warm";
  const latest = hits[0];
  const note = n === 0
    ? "這段期間沒有"
    : def.note?.(report.counts) ?? (latest ? `最近 ${when(latest.start, report.generated)}` : "");
  const Icon = def.icon;
  return (
    <li className={`${level} min-w-0 rounded-xl border border-line bg-surface px-3.5 py-2.5`} title={def.title}>
      <span className="flex items-center gap-1.5 text-xs text-fg-muted">
        <Icon className={`size-3.5 shrink-0 ${def.tone}`} aria-hidden />
        <span className="truncate">{def.label}</span>
      </span>
      <span className="block text-2xl font-semibold num leading-tight text-(--level)">
        {n > 0 ? (
          <>
            {number(n)}
            <span className="ml-1 text-xs font-normal text-fg-subtle">{def.unit}</span>
          </>
        ) : "沒有"}
      </span>
      <span className="block text-xs text-fg-subtle truncate">{note}</span>
    </li>
  );
}

export function EventSummary({ report }: { report: EventReport }) {
  return (
    <ul aria-label="摘要" className="tiles m-0 p-0 list-none">
      {(isMac(report) ? MAC_CARDS : CARDS).map((def) => <StatCard key={def.label} def={def} report={report} />)}
    </ul>
  );
}

const FILTERS: Filter[] = ["all", "crash", "gpu", "storage", "hardware", "app", "system"];
const MAC_FILTERS: Filter[] = ["all", "crash"];

export function Filters({ incidents, value, onChange, mac = false }: {
  incidents: Incident[];
  value: Filter;
  onChange: (f: Filter) => void;
  mac?: boolean; // Mac 的記錄只有當機與重開（events/mac.py）
}) {
  return (
    <div role="group" aria-label="只看哪一類" className="flex flex-wrap gap-1.5">
      {(mac ? MAC_FILTERS : FILTERS).map((f) => {
        const n = incidents.filter((i) => matches(i, f)).length;
        if (f === "system" && n === 0 && value !== f) return null; // 系統提醒很少見，沒有就不列
        const on = f === value;
        return (
          <button key={f} type="button" aria-pressed={on} disabled={n === 0 && !on && f !== "all"} onClick={() => onChange(f)}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs cursor-pointer disabled:cursor-default disabled:opacity-50 ${on ? "border-line-strong bg-surface-2 text-fg font-semibold" : "border-line text-fg-muted hover:border-line-strong hover:text-fg"}`}>
            {f === "all" ? "全部" : LABELS[f]}
            <span className="num font-normal text-fg-subtle">{n}</span>
          </button>
        );
      })}
    </div>
  );
}
