import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served under /admin by FastAPI in production; assets live at /admin/assets.
export default defineConfig({
  base: "/admin/",
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/media": "http://127.0.0.1:8080",
    },
  },
});
