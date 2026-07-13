import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Панель — SPA, раздаётся статикой за Caddy (см. ARCHITECTURE.md, docker-compose.yml).
// API backend проксируется тем же Caddy на /api и /webhooks; для dev-сервера
// прокидываем /api на локальный backend (см. README.md).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
  },
});
