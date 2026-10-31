// 同一個網頁兩種畫面：主畫面（/）與迷你視窗（/?view=mini）。主畫面有兩頁：即時監控（#live，預設）與事件記錄（#events）。
// 溫度單位從快照的 prefs 來，用 context 往下傳。在事件記錄頁時快照照樣每秒抓，切回來折線圖才不會斷。
import { useEffect, useState } from "react";
import { useEventBadge } from "../features/events/useEvents";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { TopBar } from "../features/status/StatusBar";
import type { Page } from "../features/status/StatusBar";
import { currentView, switchView } from "../lib/bridge";
import { UnitContext } from "../lib/prefs";
import { useHistory } from "../lib/useHistory";
import { useSnapshot } from "../lib/useSnapshot";
import { Dashboard } from "../pages/Dashboard";
import { Events } from "../pages/Events";
import { Mini } from "../pages/Mini";

function pageOf(hash: string): Page {
  return hash === "#events" ? "events" : "live";
}

function usePage(): Page {
  const [page, setPage] = useState(() => pageOf(window.location.hash));
  useEffect(() => {
    const onHash = () => {
      setPage(pageOf(window.location.hash));
    };
    window.addEventListener("hashchange", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
    };
  }, []);
  return page;
}

function MainApp() {
  const { snap, error } = useSnapshot();
  const series = useHistory(snap);
  const page = usePage();
  const badge = useEventBadge();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const ready = snap != null && (snap.seq > 0 || snap.state === "error");
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [page]);
  return (
    <UnitContext value={snap?.prefs?.unit ?? "C"}>
      <div className="min-h-full flex flex-col">
        <TopBar snap={snap} page={page} badge={badge} onMini={() => void switchView("mini")}
          onSettings={() => setSettingsOpen(true)} />
        {page === "events" ? (
          <Events />
        ) : ready ? (
          <Dashboard snap={snap} series={series} offline={error} />
        ) : (
          <main className="flex-1 grid place-items-center p-8 text-sm text-fg-muted">
            {error ? `${error}。` : "正在讀取感測器…"}
          </main>
        )}
      </div>
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </UnitContext>
  );
}

function MiniApp() {
  const { snap, error } = useSnapshot();
  useEffect(() => {
    document.documentElement.classList.add("mini");
    return () => {
      document.documentElement.classList.remove("mini");
    };
  }, []);
  return (
    <UnitContext value={snap?.prefs?.unit ?? "C"}>
      <Mini snap={snap} offline={error} />
    </UnitContext>
  );
}

export default function App() {
  return currentView() === "mini" ? <MiniApp /> : <MainApp />;
}
