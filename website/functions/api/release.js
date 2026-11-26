/**
 * Cloudflare Pages Function — GitHub Release Metadata Proxy for ORBIT In-App Updater & Website.
 * Proxies the latest GitHub release, enriches missing release bullets from CHANGELOG.md,
 * and exposes stable mirror URLs and SHA-256 hashes.
 */

const GITHUB_OWNER = "jainnaitk";
const GITHUB_REPO = "ORBIT";
const GITHUB_API_LATEST = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest`;
const GITHUB_WEB_LATEST = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest`;
const RAW_CHANGELOG_URL = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/main/CHANGELOG.md`;
const CACHE_TTL_SECONDS = 120;

function jsonResponse(payload, status = 200, headOnly = false) {
  return new Response(headOnly ? null : JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": status === 200 ? `public, max-age=${CACHE_TTL_SECONDS}` : "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function extractChangelogItems(body, maxItems = 4) {
  if (!body) return [];
  const items = [];
  for (const raw of String(body).split(/\r?\n/)) {
    const stripped = raw.trim();
    if (!stripped || /^#{1,6}\s+/.test(stripped) || /^[-*_]{3,}$/.test(stripped)) continue;
    const cleaned = stripped
      .replace(/^[-*+•]\s+/, "")
      .replace(/^\d+[.)]\s+/, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/[*_`~]/g, "")
      .replace(/\s+/g, " ")
      .trim();
    if (cleaned.length < 4) continue;
    if (/^(full changelog|what'?s changed|sha256|checksum|https?:\/\/)/i.test(cleaned)) continue;
    if (/github\.com\/[^/\s]+\/[^/\s]+\/compare\//i.test(cleaned)) continue;
    items.push(cleaned.length > 96 ? `${cleaned.slice(0, 93).trimEnd()}...` : cleaned);
    if (items.length >= maxItems) break;
  }
  return items;
}

function parseChangelogMarkdown(markdown, version, maxItems = 4) {
  if (!markdown) return [];
  const target = String(version || "").replace(/^[vV]/, "").trim();
  const lines = String(markdown).split(/\r?\n/);
  let inTarget = false;
  let capturedFirst = false;
  const collected = [];

  for (const raw of lines) {
    const line = raw.trim();
    const heading = line.match(/^##\s+\[?v?([0-9]+\.[0-9]+\.[0-9]+[^\]]*)\]?/i);
    if (heading) {
      if (inTarget || (capturedFirst && !target)) break;
      const headingVer = heading[1].trim();
      if (target && headingVer === target) {
        inTarget = true;
      } else if (!target && !capturedFirst) {
        inTarget = true;
        capturedFirst = true;
      }
      continue;
    }
    if (inTarget && /^[-*•]\s+/.test(line)) {
      collected.push(line);
      if (collected.length >= maxItems) break;
    }
  }

  return extractChangelogItems(collected.join("\n"), maxItems);
}

async function fetchChangelogFromRaw(version) {
  try {
    const res = await fetch(RAW_CHANGELOG_URL, {
      redirect: "follow",
      headers: { "User-Agent": "ORBIT-Release-Proxy/1.0" },
    });
    if (!res.ok) return [];
    const text = await res.text();
    const matched = parseChangelogMarkdown(text, version, 4);
    return matched.length > 0 ? matched : parseChangelogMarkdown(text, "", 4);
  } catch {
    return [];
  }
}

function parseSha256Map(text) {
  const map = new Map();
  if (!text) return map;
  for (const rawLine of text.split(/\r?\n/)) {
    const match = rawLine.trim().match(/^([a-fA-F0-9]{64})\s+\*?(.+)$/);
    if (match) {
      map.set(match[2].trim(), match[1].toLowerCase());
    }
  }
  return map;
}

async function fetchSha256Map(shaUrl) {
  if (!shaUrl) return new Map();
  try {
    const res = await fetch(shaUrl, {
      redirect: "follow",
      headers: { "User-Agent": "ORBIT-Release-Proxy/1.0" },
    });
    if (!res.ok) return new Map();
    const contentType = (res.headers.get("content-type") || "").toLowerCase();
    if (contentType.includes("text/html")) return new Map();
    return parseSha256Map(await res.text());
  } catch {
    return new Map();
  }
}

function buildMirrorUrl(origin, assetName) {
  return `${origin}/api/download?asset=${encodeURIComponent(assetName)}`;
}

function normalizeAssets(rawAssets, origin, shaMap) {
  const out = [];
  for (const asset of rawAssets || []) {
    const name = String(asset?.name || "").trim();
    if (!name.toLowerCase().endsWith(".exe")) continue;
    const browserUrl = String(asset?.browser_download_url || "").trim();
    const entry = {
      name,
      size: Number(asset?.size || 0),
      browser_download_url: browserUrl,
      mirror_download_url: buildMirrorUrl(origin, name),
    };
    const digest = shaMap.get(name);
    if (digest) entry.sha256 = digest;
    out.push(entry);
  }
  return out;
}

async function resolveChangelog(rawBody, tag) {
  const fromBody = extractChangelogItems(rawBody, 4);
  if (fromBody.length > 0) return fromBody;
  return fetchChangelogFromRaw(tag);
}

async function fetchFromGitHubApi(origin) {
  const res = await fetch(GITHUB_API_LATEST, {
    redirect: "follow",
    headers: {
      Accept: "application/vnd.github+json",
      "User-Agent": "ORBIT-Release-Proxy/1.0",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });

  if (res.status === 404) {
    return { status: 404, payload: { error: "No published GitHub release found." } };
  }
  if (!res.ok) {
    return null;
  }

  const release = await res.json();
  const rawAssets = Array.isArray(release.assets) ? release.assets : [];
  const shaAsset = rawAssets.find((a) => String(a?.name || "") === "SHA256SUMS.txt");
  const [shaMap, changelog] = await Promise.all([
    fetchSha256Map(shaAsset?.browser_download_url),
    resolveChangelog(release.body, release.tag_name),
  ]);

  return {
    status: 200,
    payload: {
      tag_name: release.tag_name,
      name: release.name,
      body: changelog.length > 0 ? changelog.map((c) => `- ${c}`).join("\n") : "",
      changelog,
      published_at: release.published_at || null,
      html_url: release.html_url || GITHUB_WEB_LATEST,
      assets: normalizeAssets(rawAssets, origin, shaMap),
      source: "github-api",
    },
  };
}

async function fetchFromGitHubWebFallback(origin) {
  const redirectRes = await fetch(GITHUB_WEB_LATEST, {
    method: "HEAD",
    redirect: "manual",
    headers: { "User-Agent": "ORBIT-Release-Proxy/1.0" },
  });

  const location = redirectRes.headers.get("location") || "";
  const tagMatch = location.match(/\/releases\/tag\/([^/?#]+)/i);
  if (!tagMatch) {
    if (redirectRes.status === 404) {
      return { status: 404, payload: { error: "No published GitHub release found." } };
    }
    return null;
  }

  const tag = decodeURIComponent(tagMatch[1]);
  const cleanVersion = tag.replace(/^[vV]/, "");
  const dlBase = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases/download/${encodeURIComponent(tag)}`;

  const [headRes, shaMap, changelog] = await Promise.all([
    fetch(`${dlBase}/ORBIT-Setup.exe`, {
      method: "HEAD",
      redirect: "follow",
      headers: { "User-Agent": "ORBIT-Release-Proxy/1.0" },
    }).catch(() => null),
    fetchSha256Map(`${dlBase}/SHA256SUMS.txt`),
    fetchChangelogFromRaw(tag),
  ]);

  const size = headRes && headRes.ok ? Number(headRes.headers.get("content-length") || 0) : 0;
  const candidateNames = Array.from(
    new Set([
      ...Array.from(shaMap.keys()).filter((name) => name.toLowerCase().endsWith(".exe")),
      `ORBIT_${cleanVersion}_x64-setup.exe`,
      "ORBIT-Setup.exe",
    ])
  );

  const assets = candidateNames.map((name) => {
    const entry = {
      name,
      size,
      browser_download_url: `${dlBase}/${encodeURIComponent(name)}`,
      mirror_download_url: buildMirrorUrl(origin, name),
    };
    const digest = shaMap.get(name);
    if (digest) entry.sha256 = digest;
    return entry;
  });

  return {
    status: 200,
    payload: {
      tag_name: tag,
      name: `ORBIT ${tag}`,
      body: changelog.length > 0 ? changelog.map((c) => `- ${c}`).join("\n") : "",
      changelog,
      published_at: null,
      html_url: `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/releases/tag/${encodeURIComponent(tag)}`,
      assets,
      source: "github-web",
    },
  };
}

async function handleReleaseRequest(context, headOnly) {
  const reqUrl = new URL(context.request.url);
  const origin = reqUrl.origin;

  const cache = typeof caches !== "undefined" ? caches.default : null;
  const cacheKey = new Request(`${origin}/api/release`, { method: "GET" });

  if (!headOnly && cache) {
    try {
      const cached = await cache.match(cacheKey);
      if (cached) return cached;
    } catch {
      // Ignore cache read failures
    }
  }

  let result = null;
  try {
    result = await fetchFromGitHubApi(origin);
  } catch {
    result = null;
  }

  if (!result) {
    try {
      result = await fetchFromGitHubWebFallback(origin);
    } catch {
      result = null;
    }
  }

  if (!result) {
    return jsonResponse({ error: "Unable to query GitHub Releases upstream." }, 502, headOnly);
  }

  const res = jsonResponse(result.payload, result.status, headOnly);
  if (!headOnly && result.status === 200 && cache) {
    try {
      context.waitUntil(cache.put(cacheKey, res.clone()));
    } catch {
      // Ignore cache write failures
    }
  }
  return res;
}

export async function onRequestGet(context) {
  return handleReleaseRequest(context, false);
}

export async function onRequestHead(context) {
  return handleReleaseRequest(context, true);
}
