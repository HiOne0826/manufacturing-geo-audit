import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/manufacturing-geo-audit/" : "/",
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/manufacturing-geo-audit/api": {
        target: "http://127.0.0.1:8765",
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
