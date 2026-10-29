// 顯示偏好（目前只有溫度單位）：來自快照的 prefs，所以主畫面改了設定，迷你視窗一秒內也會跟著變。
import { createContext, useContext } from "react";
import type { Unit } from "../types";

export const UnitContext = createContext<Unit>("C");

export function useUnit(): Unit {
  return useContext(UnitContext);
}
