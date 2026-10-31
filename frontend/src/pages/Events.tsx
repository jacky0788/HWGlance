// 事件記錄頁：把 Windows 的系統與應用程式記錄整理成一件件事（後端 src/hwglance/events/，GET /api/events），
// 排查當機與硬體故障。只負責排版，內容在 features/events/。
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Segmented } from "../components/Segmented";
import { EventSummary, Filters } from "../features/events/EventSummary";
import { IncidentList } from "../features/events/IncidentList";
import { matches } from "../features/events/meta";
import type { Filter } from "../features/events/meta";
import { useEventReport } from "../features/events/useEvents";
import { Notes } from "../features/status/StatusBar";
import { clockTime, fullDate } from "../lib/format";

const RANGES = [
  { value: 7, text: "7 天" },
  { value: 30, text: "30 天" },
  { value: 90, text: "90 天" },
  { value: 0, text: "全部" },
];

export function Events() {
  const [days, setDays] = useState(30);
  const [filter, setFilter] = useState<Filter>("all");
  const { report, error, loading } = useEventReport(days, 2 * 60_000);
  const stale = report != null && report.days !== (days || null); // 換了範圍、新的還沒到
  return (
    <>
      <main className="flex-1 px-5 py-4 space-y-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {/* 說明至少留 18rem，窄的時候範圍按鈕換到下一行，不要把說明擠成一長條 */}
          <div className="min-w-0 flex-[1_1_18rem]">
            <h2 className="m-0 text-sm font-semibold text-fg-muted">事件記錄</h2>
            <p className="m-0 text-xs text-fg-subtle">Windows 記下的當機、硬體與程式錯誤，整理成一件件事，看得出出事前發生了什麼。</p>
          </div>
          {loading && report ? <RefreshCw className="size-3.5 shrink-0 animate-spin text-fg-subtle" aria-label="讀取中" /> : null}
          <Segmented label="時間範圍" value={days} onChange={setDays} options={RANGES} />
        </div>
        {report == null ? (
          <p className="m-0 py-10 text-sm text-fg-muted text-center">{error ? `${error}。` : "正在讀取事件記錄…"}</p>
        ) : (
          <div className={`space-y-4 transition-opacity ${stale ? "opacity-60" : ""}`}>
            <Notes notes={report.errors} offline={error} />
            <EventSummary report={report} />
            <Filters incidents={report.incidents} value={filter} onChange={setFilter} />
            <IncidentList incidents={report.incidents.filter((i) => matches(i, filter))} markers={report.markers}
              now={report.generated} collapse={filter === "all"}
              empty={filter === "all" ? "這段期間沒有記到當機、硬體錯誤或程式當掉。" : "這段期間沒有這類事件。"} />
          </div>
        )}
      </main>
      {report ? (
        <footer className="px-5 pb-4 text-xs text-fg-subtle num">
          {[report.oldest ? `系統記錄最早到 ${fullDate(report.oldest)}` : null, `整理於 ${clockTime(report.generated)}`,
            "只讀取，不會改動 Windows 的記錄"].filter(Boolean).join("・")}
        </footer>
      ) : null}
    </>
  );
}
