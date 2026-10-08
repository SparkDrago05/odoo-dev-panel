import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// Apply the saved theme before the first paint, so a light-theme user never sees a dark flash.
try {
  const prefs = JSON.parse(localStorage.getItem("odp.prefs") ?? "{}");
  const theme = prefs.theme === "light" || prefs.theme === "dark" ? prefs.theme
    : prefs.theme === "system" && window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  if (prefs.density) document.documentElement.dataset.density = prefs.density;
} catch {
  document.documentElement.dataset.theme = "dark";
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
