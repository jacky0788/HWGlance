import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 平常用 `npm run build`：dist/ 由 python -m hwglance 送出（會把 token 寫進 index.html）。
// `npm run dev`（5173）：API 轉給示範資料的伺服器 python -m hwglance --serve --demo --port 8797。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8797" },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
