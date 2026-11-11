// 主畫面有三頁：即時監控（#live，預設）、事件記錄（#events）、暫存檔（#temp）。迷你模式不是網頁，是本體的疊加層視窗（後端 mini/）。
// 這台沒有的功能（快照的 source.features，lib/features.ts；Mac 版沒有事件記錄、暫存檔、迷你模式、系統匣）就不出現。
// 溫度單位從快照的 prefs 來，用 context 往下傳；卡片顏色也從 prefs 來，寫在 <html> 上（lib/colors.ts）。
// 在事件記錄頁時快照照樣每秒抓，切回來折線圖才不會斷。
import { useCallback, useEffect, useState } from "react";
import { useEventBadge } from "../features/events/useEvents";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { TopBar } from "../features/status/StatusBar";
import type { Page } from "../features/status/StatusBar";
import { api } from "../lib/api";
import { useKindColors } from "../lib/colors";
import { hasFeature } from "../lib/features";
import { UnitContext } from "../lib/prefs";
import { useHistory } from "../lib/useHistory";
import { useSnapshot } from "../lib/useSnapshot";
import { Dashboard } from "../pages/Dashboard";
import { Events } from "../pages/Events";
import { Temp } from "../pages/Temp";

function pageOf(hash: string): Page {
  return hash === "#events" ? "events" : hash === "#temp" ? "temp" : "live";
}

// 迷你模式右鍵的「迷你模式設定…」開主畫面時帶 #settings/mini：直接打開設定頁、捲到那一段，網址換回 #live
function useSettingsLink(open: (section: string | undefined) => void) {
  useEffect(() => {
    const check = () => {
      const hash = window.location.hash;
      if (!hash.startsWith("#settings")) return;
      history.replaceState(null, "", "#live");
      open(hash.split("/")[1]);
    };
    check();
    window.addEventListener("hashchange", check);
    return () => {
      window.removeEventListener("hashchange", check);
    };
  }, [open]);
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

export default function App() {
  const { snap, error } = useSnapshot();
  const series = useHistory(snap);
  const asked = usePage();
  // 這台沒有的頁（Mac 版沒有事件記錄、暫存檔）就留在即時監控
  const page: Page = (asked === "events" && !hasFeature(snap, "events")) || (asked === "temp" && !hasFeature(snap, "temp"))
    ? "live"
    : asked;
  const badge = useEventBadge(hasFeature(snap, "events"));
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<string | undefined>();
  const ready = snap != null && (snap.seq > 0 || snap.state === "error");
  useKindColors(snap?.prefs?.colors);
  useSettingsLink(useCallback((section: string | undefined) => {
    setSettingsSection(section);
    setSettingsOpen(true);
  }, []));
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [page]);
  return (
    <UnitContext value={snap?.prefs?.unit ?? "C"}>
      <div className="min-h-full flex flex-col">
        <TopBar snap={snap} page={page} badge={badge} onMini={() => void api.openUi("mini").catch(() => undefined)}
          onSettings={() => setSettingsOpen(true)} />
        {page === "events" ? (
          <Events />
        ) : page === "temp" ? (
          <Temp />
        ) : ready ? (
          <Dashboard snap={snap} series={series} offline={error} />
        ) : (
          <main className="flex-1 grid place-items-center p-8 text-sm text-fg-muted">
            {error ? `${error}。` : "正在讀取感測器…"}
          </main>
        )}
      </div>
      <SettingsDialog open={settingsOpen} section={settingsSection} tray={hasFeature(snap, "tray")}
        mini={hasFeature(snap, "mini")} onClose={() => {
        setSettingsOpen(false);
        setSettingsSection(undefined);
      }} />
    </UnitContext>
  );
}
