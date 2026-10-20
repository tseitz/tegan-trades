import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// scripts/dev.sh sets DASHBOARD_PORT to whatever port it started the backend on. A bare
// `pnpm --dir web dev` assumes `dashboard`'s default.
const dashboardPort = process.env.DASHBOARD_PORT ?? "8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": `http://127.0.0.1:${dashboardPort}`,
    },
  },
});
