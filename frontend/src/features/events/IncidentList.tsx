// 事件清單：新的在前，一件事一列（標題、標籤、時間、先後經過）；點開看每一筆記錄（再點看 Windows 的原文）、
// 這是什麼、可以怎麼做。看「全部」時，連續的零星事件（零星的顯示卡錯誤、單獨的程式當掉）收成一行，
// 要注意的事才不會被洗掉；選了某一類就全部列出來。
import { ChevronRight, CircleCheck } from "lucide-react";
import { useState } from "react";
import { day, daySpan, gap, hm, hms, number, span, when } from "../../lib/format";
import type { EventCategory, EventLine, EventMarker, Incident } from "../../types";
import { iconOf, levelClass } from "./meta";

const LOGS: Record<string, string> = { System: "系統記錄", Application: "應用程式記錄" };
const MINOR: Record<EventCategory, string> = {
  crash: "當機", gpu: "顯示卡小錯誤", storage: "硬碟小錯誤", hardware: "硬體提醒", system: "系統提醒", app: "程式當掉",
};

type Row = { incident: Incident } | { minor: Incident[] };

function rowsOf(incidents: Incident[], collapse: boolean): Row[] {
  if (!collapse) return incidents.map((incident) => ({ incident }));
  const rows: Row[] = [];
  let run: Incident[] = [];
  const flush = () => {
    if (run.length === 1) rows.push({ incident: run[0] });
    if (run.length > 1) rows.push({ minor: run });
    run = [];
  };
  for (const i of incidents) {
    if (i.major) {
      flush();
      rows.push({ incident: i });
    } else {
      run.push(i);
    }
  }
  flush();
  return rows;
}

function LineBody({ line, showDay, now }: { line: EventLine; showDay: boolean; now: number }) {
  const until = day(line.last, now) === day(line.time, now) ? hm(line.last) : when(line.last, now);
  return (
    <>
      <span className="w-[4.25rem] shrink-0 pt-0.5 text-xs text-fg-subtle num whitespace-nowrap">
        {showDay ? <span className="block">{day(line.time, now)}</span> : null}
        {hms(line.time)}
      </span>
      <span aria-hidden className="mt-2 size-1.5 shrink-0 rounded-full bg-(--level)" />
      {/* .NET 的堆疊（LibreHardwareMonitor.Interop.NvidiaML…）很長又沒有空白，窄的時候要能斷開 */}
      <span className="min-w-0 flex-1 break-words">
        <span className="text-sm">{line.text}</span>
        {line.count > 1 ? (
          <span className="ml-1.5 text-xs text-fg-muted num whitespace-nowrap">
            ×{number(line.count)}{line.last - line.time >= 60 ? `，到 ${until}` : ""}
          </span>
        ) : null}
        <span className="block text-xs text-fg-subtle">
          {line.source}・{LOGS[line.log] ?? line.log}
          {line.message ? <span className="text-accent">・原文</span> : null}
        </span>
      </span>
    </>
  );
}

function Line({ line, showDay, now }: { line: EventLine; showDay: boolean; now: number }) {
  const cls = `${levelClass(line.level)} border-t border-line first:border-t-0`;
  if (!line.message) {
    return (
      <li className={cls}>
        <div className="flex items-start gap-2.5 py-1.5"><LineBody line={line} showDay={showDay} now={now} /></div>
      </li>
    );
  }
  return (
    <li className={cls}>
      <details>
        <summary className="flex items-start gap-2.5 py-1.5 cursor-pointer list-none [&::-webkit-details-marker]:hidden">
          <LineBody line={line} showDay={showDay} now={now} />
        </summary>
        <pre className="m-0 mb-2 sm:ml-[5.875rem] whitespace-pre-wrap break-words rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs text-fg-muted">
          {line.message}
        </pre>
      </details>
    </li>
  );
}

function IncidentBody({ incident: i, markers, now }: { incident: Incident; markers: EventMarker[]; now: number }) {
  const showDay = day(i.start, now) !== day(i.end, now);
  const before = markers.find((m) => m.time < i.start); // markers 新的在前：第一個比它早的就是前一次
  return (
    <div className="pl-11 pr-4 pb-4 space-y-4">
      <section>
        <h4 className="m-0 mb-1 text-xs font-semibold text-fg-muted">經過</h4>
        <ol className="m-0 p-0 list-none">
          {i.lines.map((line, n) => <Line key={`${n}:${line.source}`} line={line} showDay={showDay} now={now} />)}
        </ol>
        {before ? (
          <p className="m-0 mt-1 text-xs text-fg-subtle">
            前一次開關機或睡眠：{when(before.time, now)} {before.text}，出事前 {gap(i.start - before.time)}
          </p>
        ) : null}
      </section>
      {i.explain.length > 0 ? (
        <section>
          <h4 className="m-0 mb-1 text-xs font-semibold text-fg-muted">這是什麼</h4>
          <div className="space-y-1">
            {i.explain.map((p) => <p key={p} className="m-0 text-sm text-fg-muted">{p}</p>)}
          </div>
        </section>
      ) : null}
      {i.advice.length > 0 ? (
        <section>
          <h4 className="m-0 mb-1 text-xs font-semibold text-fg-muted">可以怎麼做</h4>
          <ol className="m-0 pl-5 list-decimal space-y-0.5 text-sm text-fg-muted">
            {i.advice.map((a) => <li key={a}>{a}</li>)}
          </ol>
        </section>
      ) : null}
    </div>
  );
}

function IncidentRow({ incident: i, markers, now }: { incident: Incident; markers: EventMarker[]; now: number }) {
  const [open, setOpen] = useState(false);
  const { Icon, tone } = iconOf(i);
  return (
    <li>
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className="w-full flex items-start gap-3 px-4 py-3 text-left cursor-pointer hover:bg-surface-2 transition-colors">
        <Icon className={`size-4 mt-0.5 shrink-0 ${tone}`} aria-hidden />
        <span className="min-w-0 flex-1 break-words">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="text-sm font-semibold">{i.title}</span>
            <span className={`${levelClass(i.level)} rounded-md bg-(--level-soft) px-1.5 text-xs text-(--level) whitespace-nowrap`}>
              {i.badge}
            </span>
            <span className="ml-auto text-xs text-fg-subtle num whitespace-nowrap">{span(i.start, i.end, now)}</span>
          </span>
          {i.summary && i.summary !== i.title ? <span className="block mt-0.5 text-xs text-fg-muted">{i.summary}</span> : null}
        </span>
        <ChevronRight className={`size-4 mt-0.5 shrink-0 text-fg-subtle transition-transform ${open ? "rotate-90" : ""}`} aria-hidden />
      </button>
      {open ? <IncidentBody incident={i} markers={markers} now={now} /> : null}
    </li>
  );
}

function MinorRun({ items, markers, now }: { items: Incident[]; markers: EventMarker[]; now: number }) {
  const [open, setOpen] = useState(false);
  const counts = new Map<EventCategory, number>();
  for (const i of items) counts.set(i.category, (counts.get(i.category) ?? 0) + 1);
  const parts = [...counts].map(([cat, n]) => `${MINOR[cat]} ${n}`).join("、");
  return (
    <li>
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left text-xs text-fg-muted cursor-pointer hover:bg-surface-2 transition-colors">
        <ChevronRight className={`size-4 shrink-0 text-fg-subtle transition-transform ${open ? "rotate-90" : ""}`} aria-hidden />
        <span className="min-w-0 flex-1">另外 {items.length} 件零星的事：{parts}</span>
        <span className="num whitespace-nowrap text-fg-subtle">{daySpan(items[items.length - 1].start, items[0].end, now)}</span>
      </button>
      {open ? (
        <ol className="m-0 p-0 list-none border-t border-line divide-y divide-line bg-page">
          {items.map((i) => <IncidentRow key={i.id} incident={i} markers={markers} now={now} />)}
        </ol>
      ) : null}
    </li>
  );
}

export function IncidentList({ incidents, markers, now, collapse, empty }: {
  incidents: Incident[]; // 新的在前
  markers: EventMarker[];
  now: number; // 後端產生報告的時間（「今天」「昨天」照這個算）
  collapse: boolean;
  empty: string;
}) {
  if (incidents.length === 0) {
    return (
      <p className="level-ok m-0 flex items-center justify-center gap-2 rounded-2xl border border-line bg-surface px-4 py-8 text-sm text-fg-muted">
        <CircleCheck className="size-4 shrink-0 text-(--level)" aria-hidden />
        {empty}
      </p>
    );
  }
  return (
    <ol className="m-0 p-0 list-none rounded-2xl border border-line bg-surface divide-y divide-line overflow-hidden">
      {rowsOf(incidents, collapse).map((r) =>
        "minor" in r
          ? <MinorRun key={`minor:${r.minor[0].id}`} items={r.minor} markers={markers} now={now} />
          : <IncidentRow key={r.incident.id} incident={r.incident} markers={markers} now={now} />)}
    </ol>
  );
}
