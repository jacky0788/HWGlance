// 卡片顏色（設定頁「顏色」）：調色盤是 tokens.css 的 --swatch-*，自訂色照深淺色自動調亮暗。
// 只把改過的寫到 <html> 的 --kind-* 上；沒改過的拿掉，用回 tokens.css 的預設（跟以前一模一樣）。
import { useEffect } from "react";

export const COLOR_KINDS = ["cpu", "gpu", "board", "memory", "storage"] as const;

export const PRESET_NAMES: Record<string, string> = {
  blue: "藍", indigo: "靛", purple: "紫", pink: "粉紅", teal: "青綠", sky: "天藍", lime: "萊姆綠", silver: "銀白",
};

// 自訂色：深色模式至少要這麼亮、淺色模式至少要這麼深（oklch 的亮度），圖示和細線才看得清楚
export function kindColor(value: string): string {
  if (value.startsWith("#")) {
    return `light-dark(oklch(from ${value} min(l, 0.5) c h), oklch(from ${value} max(l, 0.72) c h))`;
  }
  return `var(--swatch-${value})`;
}

/** changed：只有改過的 {種類: 顏色}（快照的 prefs.colors；設定頁預覽時是畫面上正在選的） */
export function applyKindColors(changed: Record<string, string> | undefined): void {
  const style = document.documentElement.style;
  for (const kind of COLOR_KINDS) {
    const value = changed?.[kind];
    if (value) style.setProperty(`--kind-${kind}`, kindColor(value));
    else style.removeProperty(`--kind-${kind}`);
  }
}

export function useKindColors(changed: Record<string, string> | undefined): void {
  const key = JSON.stringify(changed ?? {});
  useEffect(() => {
    applyKindColors(JSON.parse(key) as Record<string, string>);
  }, [key]);
}

/** 只留跟預設不一樣的（後端存檔時也是這樣挑） */
export function changedColors(colors: Record<string, string>, defaults: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(colors).filter(([kind, value]) => value !== defaults[kind]));
}

/** 調色盤某個顏色現在（深色或淺色模式）的實際值：給「自訂」取色器當起點 */
export function swatchHex(name: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(`--swatch-${name}`).trim().toLowerCase();
  return /^#[0-9a-f]{6}$/.test(value) ? value : "#808080";
}
