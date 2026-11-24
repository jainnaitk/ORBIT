/**
 * Cloudflare Pages Function — Same-Origin GitHub Release Installer Streamer
 * Streams the latest ORBIT Windows (.exe) installer so Chromium/Edge opens the
 * native OS "Save As" File Explorer dialog directly from the ORBIT website.
 */

const GITHUB_OWNER = "jainnaitk";
const GITHUB_REPO = "ORBIT";
const DEFAULT_ASSET = "ORBIT-Setup.exe";
const SAFE_ASSET_RE = /^[A-Za-z0-9._-]+\.exe$/i;

async function handleDownload(context, headOnly) {
  const reqUrl = new URL(context.request.url);
  const requested = (reqUrl.searchParams.get("asset") || "").trim();
  const filename = SAFE_ASSET_RE.test(requested) ? requested : DEFAULT_ASSET;

  const upstream = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest/download/${encodeURIComponent(filename)}`;

  const res = await fetch(upstream, {
    method: headOnly ? "HEAD" : "GET",
    redirect: "follow",
    headers: {
      "User-Agent": "ORBIT-Website-Downloader/1.0",
    },
  });

  if (!res.ok || (!headOnly && !res.body)) {
    return new Response(
      headOnly ? null : "Latest ORBIT Windows installer not found on GitHub Releases yet.",
      {
        status: 404,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      }
    );
  }

  const headers = new Headers();
  headers.set("Content-Type", "application/octet-stream");
  headers.set("Content-Disposition", `attachment; filename="${filename}"`);
  headers.set("Cache-Control", "no-store");
  const contentLen = res.headers.get("content-length");
  if (contentLen) {
    headers.set("Content-Length", contentLen);
  }

  return new Response(headOnly ? null : res.body, { status: 200, headers });
}

export async function onRequestGet(context) {
  return handleDownload(context, false);
}

export async function onRequestHead(context) {
  return handleDownload(context, true);
}
