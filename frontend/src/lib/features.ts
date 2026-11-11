// 這台有哪些功能：快照的 source.features（後端 sensors/service.py 的 ALL_FEATURES）。
// Mac 版還沒有事件記錄、暫存檔、迷你模式、系統匣，那些分頁、按鈕、設定就不出現。
// 快照還沒到（剛打開）時當成都有：Windows 的畫面跟以前一樣，不會先少再多。
import type { Feature, Snapshot } from "../types";

export function hasFeature(snap: Snapshot | null, feature: Feature): boolean {
  const list = snap?.source.features;
  return list == null || list.includes(feature);
}
