/**
 * Cloudflare Pages Function — Same-Origin GitHub Release Installer Streamer
 * Enables the browser's native "Save As" File Explorer dialog (showSaveFilePicker)
 * directly from the ORBIT website without navigating to GitHub.
 */

const GITHUB_OWNER = "jainnaitk";
const GITHUB_REPO = "ORBIT";
const DEFAULT_ASSET = "ORBIT-Setup.exe";
const SAFE_ASSET_RE = /^[A-Za-z0-9._-]+\.(exe|msi)$/i;

export async function onRequestGet(context) {
  const reqUrl = new URL(context.request.url);
  const requested = (reqUrl.searchParams.get("asset") || "").trim();
  const filename = SAFE_ASSET_RE.test(requested) ? requested : DEFAULT_ASSET;

  const upstream = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest/download/${encodeURIComponent(filename)}`;

  const res = await fetch(upstream, {
    redirect: "follow",
    headers: {
      "User-Agent": "ORBIT-Website-Downloader/1.0",
    },
  });

  if (!res.ok || !res.body) {
    return new Response("Latest ORBIT Windows installer not found on GitHub Releases yet.", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }

  const headers = new Headers();
  headers.set("Content-Type", "application/octet-stream");
  headers.set("Content-Disposition", `attachment; filename="${filename}"`);
  headers.set("Cache-Control", "no-store");
  const contentLen = res.headers.get("content-length");
  if (contentLen) {
    headers.set("Content-Length", contentLen);
  }

  return new Response(res.body, { status: 200, headers });
}
