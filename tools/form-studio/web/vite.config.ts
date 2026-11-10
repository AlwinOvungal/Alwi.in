import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built UI is served by the Python backend (backend/formstudio/web_dist).
// During `npm run dev`, API calls are proxied to a backend on :8765.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/formstudio/web_dist", emptyOutDir: true, chunkSizeWarningLimit: 1600 },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
