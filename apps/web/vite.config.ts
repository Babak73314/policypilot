import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/** The API serves these path prefixes; everything else is the SPA (docs/design/api.md, routing matrix). */
const API_PREFIXES = ["/api", "/webhooks", "/mcp", "/dev/", "/healthz", "/readyz"] as const;
// 127.0.0.1, not localhost: Node may resolve localhost to ::1 first, where Docker is not listening.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";

const proxy = Object.fromEntries(API_PREFIXES.map((prefix) => [prefix, { target: API_TARGET }]));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy,
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy,
  },
  build: {
    target: "es2023",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
    unstubGlobals: true,
  },
});
