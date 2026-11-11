import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App";
import { startMacShell } from "./lib/macShell";
import "./styles/index.css";

startMacShell();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
