// 同一個網頁兩種畫面：主畫面（/）與迷你視窗（/?view=mini）。溫度單位從快照的 prefs 來，用 context 往下傳。
import { useEffect, useState } from "react";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { TopBar } from "../features/status/StatusBar";
import { currentView, switchView } from "../lib/bridge";
import { UnitContext } from "../lib/prefs";
import { useHistory } from "../lib/useHistory";
import { useSnapshot } from "../lib/useSnapshot";
import { Dashboard } from "../pages/Dashboard";
import { Mini } from "../pages/Mini";

function MainApp() {
  const { snap, error } = useSnapshot();
  const series = useHistory(snap);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const ready = snap != null && (snap.seq > 0 || snap.state === "error");
  return (
    <UnitContext value={snap?.prefs?.unit ?? "C"}>
      <div className="min-h-full flex flex-col">
        <TopBar snap={snap} onMini={() => void switchView("mini")} onSettings={() => setSettingsOpen(true)} />
        {ready ? (
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
