import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Authoritative port for the dev proxy — `dashboard.cli`'s `--port` is only for a collision
// and does not update this, so changing one without the other silently breaks `pnpm --dir web
// dev`.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
