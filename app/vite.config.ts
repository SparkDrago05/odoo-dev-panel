import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  // The bundle loads from disk inside the app, so one ~650 kB chunk costs nothing worth splitting for.
  build: { target: "es2022", chunkSizeWarningLimit: 900 },
});
