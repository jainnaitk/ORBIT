import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";

// Plugin to serve bridge configuration during local browser development
function bridgeConfigDevPlugin() {
  return {
    name: "bridge-config-dev",
    configureServer(server: any) {
      server.middlewares.use("/__bridge_config", (_req: any, res: any) => {
        try {
          const localAppData =
            process.env.LOCALAPPDATA ||
            (process.env.USERPROFILE
              ? path.join(process.env.USERPROFILE, "AppData", "Local")
              : "");
          const bridgePath = path.join(localAppData, "metrolite", "bridge.json");
          if (fs.existsSync(bridgePath)) {
            const data = fs.readFileSync(bridgePath, "utf-8");
            res.setHeader("Content-Type", "application/json");
            res.end(data);
            return;
          }
        } catch {
          // ignore
        }
        res.statusCode = 404;
        res.end(JSON.stringify({ error: "not found" }));
      });
    },
  };
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), bridgeConfigDevPlugin()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  envPrefix: ["VITE_", "TAURI_ENV_*"],
  build: {
    target: process.env.TAURI_ENV_PLATFORM == "windows" ? "chrome105" : "safari13",
    minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
  },
});
