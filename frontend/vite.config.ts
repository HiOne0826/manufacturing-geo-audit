import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8765";

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/manufacturing-geo-audit/" : "/",
  plugins: [react()],
  server: {
    proxy: {
      "/api": apiProxyTarget,
      "/manufacturing-geo-audit/api": {
        target: apiProxyTarget,
        rewrite: (path) => path.replace(/^\/manufacturing-geo-audit/, "")
      }
    }
  },
  build: {
    outDir: "dist",
    emptyOutDir: true
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    clearMocks: true,
    include: ["src/**/*.test.{ts,tsx}"]
  }
}));
