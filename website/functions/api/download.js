/**
 * Cloudflare Pages Function — Same-Origin GitHub Release Installer Streamer
 * Streams the latest ORBIT Windows (.exe) installer so Chromium/Edge opens the
 * native OS "Save As" File Explorer dialog directly from the ORBIT website.
 */

const GITHUB_OWNER = "jainnaitk";
const GITHUB_REPO = "ORBIT";
const DEFAULT_ASSET = "ORBIT-Setup.exe";
const SAFE_ASSET_RE = /^[A-Za-z0-9._-]+\.exe$/i;

async function fetchReleaseAsset(assetName, method, rangeHeader) {
  const upstream = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest/download/${encodeURIComponent(assetName)}`;
  const reqHeaders = { "User-Agent": "ORBIT-Website-Downloader/1.0" };
  if (rangeHeader && method === "GET") {
    reqHeaders["Range"] = rangeHeader;
  }
  return fetch(upstream, {
    method,
    redirect: "follow",
    headers: reqHeaders,
  });
}

async function handleDownload(context, headOnly) {
  const reqUrl = new URL(context.request.url);
  const requested = (reqUrl.searchParams.get("asset") || "").trim();
  let filename = SAFE_ASSET_RE.test(requested) ? requested : DEFAULT_ASSET;
  const method = headOnly ? "HEAD" : "GET";
  const rangeHeader = context.request.headers.get("range");

  let res = await fetchReleaseAsset(filename, method, rangeHeader);
  if (!res.ok && filename !== DEFAULT_ASSET) {
    filename = DEFAULT_ASSET;
    res = await fetchReleaseAsset(filename, method, rangeHeader);
  }

  const upstreamType = (res.headers.get("content-type") || "").toLowerCase();
  if (!res.ok || upstreamType.includes("text/html") || (!headOnly && !res.body)) {
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
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Cache-Control", "no-store");
  for (const h of ["content-length", "content-range", "accept-ranges"]) {
    const val = res.headers.get(h);
    if (val) headers.set(h, val);
  }

  const status = res.status === 206 ? 206 : 200;
  return new Response(headOnly ? null : res.body, { status, headers });
}

export async function onRequestGet(context) {
  return handleDownload(context, false);
}

export async function onRequestHead(context) {
  return handleDownload(context, true);
}
