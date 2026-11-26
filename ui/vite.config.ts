import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";

// Plugin to serve bridge configuration during local browser development
function bridgeConfigDevPlugin() {
  const isLocalOrigin = (origin?: string): boolean => {
    if (!origin) return true;
    try {
      const parsed = new URL(origin);
      return (
        (parsed.protocol === "http:" || parsed.protocol === "https:") &&
        (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1")
      );
    } catch {
      return false;
    }
  };

  return {
    name: "bridge-config-dev",
    configureServer(server: any) {
      server.middlewares.use("/__bridge_config", (req: any, res: any) => {
        res.setHeader("Cache-Control", "no-store");
        const origin = req.headers?.origin as string | undefined;
        const secFetchSite = req.headers?.["sec-fetch-site"] as string | undefined;
        if (!isLocalOrigin(origin) || (secFetchSite && secFetchSite !== "same-origin" && secFetchSite !== "none")) {
          res.statusCode = 403;
          res.end(JSON.stringify({ error: "forbidden" }));
          return;
        }
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

const pkgJson = JSON.parse(fs.readFileSync(path.resolve(__dirname, "package.json"), "utf-8"));

// https://vitejs.dev/config/
export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkgJson.version || "1.2.6"),
  },
  plugins: [react(), bridgeConfigDevPlugin()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
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
