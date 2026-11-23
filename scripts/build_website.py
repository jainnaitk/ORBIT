"""Build production ORBIT/website/index.html from v6 showcase with GitHub auto-release & Save As."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "orbit_event_horizon_edition_3d_product_showcase-v6.html"
DST = ROOT / "website" / "index.html"

OLD_MODAL_HEADER = """<span class="text-[10px] font-mono uppercase tracking-[0.2em]" style="color: var(--accent);">Windows 10 / 11 • v0.1.0</span>"""
NEW_MODAL_HEADER = """<span id="modal-version-badge" class="text-[10px] font-mono uppercase tracking-[0.2em]" style="color: var(--accent);">Windows 10 / 11 • Latest Release</span>"""

OLD_INSTALLER_PANEL = """      <!-- Tab 1: Direct Installer & Repository -->
      <div id="modal-panel-installer" class="space-y-3">
        <div class="rounded-2xl bg-[#010104] border border-white/[0.09] p-4 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div class="flex items-center gap-3.5 text-center sm:text-left">
            <div class="w-12 h-12 rounded-2xl bg-black ring-1 ring-white/15 overflow-hidden flex-none">
              <img data-orbit-logo alt="ORBIT Installer" class="w-full h-full object-cover scale-125" />
            </div>
            <div>
              <div class="text-sm font-bold text-[#F2F5FC]">ORBIT_0.1.0_x64-setup.exe</div>
              <div class="text-[11px] font-mono text-[#8E98B5] mt-0.5">Windows 10/11 64-bit • ~14.2 MB • Native Tauri v2</div>
            </div>
          </div>
          <div class="flex items-center gap-2 w-full sm:w-auto flex-none">
            <a id="dl-exe-link" href="./downloads/ORBIT_0.1.0_x64-setup.exe" download="ORBIT_0.1.0_x64-setup.exe"
               onclick="showToast('Starting download: ORBIT_0.1.0_x64-setup.exe')"
               class="btn-platinum flex-1 sm:flex-none px-5 py-2.5 rounded-full text-xs font-semibold active:scale-95 inline-flex items-center justify-center gap-2">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
              <span>Download .exe</span>
            </a>
            <a id="dl-msi-link" href="./downloads/ORBIT_0.1.0_x64_en-US.msi" download="ORBIT_0.1.0_x64_en-US.msi"
               onclick="showToast('Starting download: ORBIT_0.1.0_x64_en-US.msi')"
               class="px-3.5 py-2.5 rounded-full text-xs font-mono font-semibold bg-[#080B14] hover:bg-[#0E1322] text-[#F2F5FC] border border-white/[0.10] active:scale-95 transition-colors inline-flex items-center justify-center">
              .msi
            </a>
          </div>
        </div>
        <div class="flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-[#8E98B5]">
          <span>Includes bundled Tauri v2 WebView shell + offline Opus cache support.</span>
          <a id="dl-releases-link" href="https://github.com/naitikjain/orbit/releases/latest" target="_blank" rel="noopener noreferrer"
             class="font-mono text-[#F2F5FC] hover:underline inline-flex items-center gap-1" style="color: var(--accent);">
            <span>GitHub Releases ↗</span>
          </a>
        </div>
      </div>"""

NEW_INSTALLER_PANEL = """      <!-- Tab 1: Direct Installer & Repository (Auto-Synced with GitHub Releases + Native Save As) -->
      <div id="modal-panel-installer" class="space-y-3">
        <div class="rounded-2xl bg-[#010104] border border-white/[0.09] p-4 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div class="flex items-center gap-3.5 text-center sm:text-left min-w-0">
            <div class="w-12 h-12 rounded-2xl bg-black ring-1 ring-white/15 overflow-hidden flex-none">
              <img data-orbit-logo alt="ORBIT Installer" class="w-full h-full object-cover scale-125" />
            </div>
            <div class="min-w-0">
              <div id="dl-exe-filename" class="text-sm font-bold text-[#F2F5FC] truncate">ORBIT-Setup.exe</div>
              <div id="dl-exe-meta" class="text-[11px] font-mono text-[#8E98B5] mt-0.5">Windows 10/11 64-bit • Native Tauri v2</div>
            </div>
          </div>
          <div class="flex items-center gap-2 w-full sm:w-auto flex-none">
            <button type="button" id="dl-exe-btn" onclick="downloadOrbitAsset('exe')"
               class="btn-platinum flex-1 sm:flex-none px-5 py-2.5 rounded-full text-xs font-semibold active:scale-95 inline-flex items-center justify-center gap-2">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
              <span id="dl-exe-label">Download .exe</span>
            </button>
            <button type="button" id="dl-msi-btn" onclick="downloadOrbitAsset('msi')"
               class="hidden px-3.5 py-2.5 rounded-full text-xs font-mono font-semibold bg-[#080B14] hover:bg-[#0E1322] text-[#F2F5FC] border border-white/[0.10] active:scale-95 transition-colors inline-flex items-center justify-center">
              .msi
            </button>
          </div>
        </div>
        <div id="dl-progress-wrap" class="hidden rounded-xl bg-[#080B14] border border-white/[0.08] px-3.5 py-2.5 space-y-1.5">
          <div class="flex items-center justify-between text-[11px] font-mono">
            <span id="dl-progress-text" class="text-[#F4F6FF]">Saving installer...</span>
            <span id="dl-progress-pct" class="tabular-nums" style="color: var(--accent);">0%</span>
          </div>
          <div class="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
            <div id="dl-progress-fill" class="w-full h-full rounded-full origin-left will-change-transform" style="transform: scaleX(0); background: linear-gradient(90deg, var(--secondary), var(--accent));"></div>
          </div>
        </div>
        <div class="rounded-xl bg-[#080B14] border border-white/[0.07] px-3.5 py-2 text-[11px] text-[#939DBB] leading-relaxed">
          <strong class="text-[#F4F6FF]">Windows SmartScreen note:</strong> On first run, click <em>More info &rarr; Run anyway</em>. Check <code class="font-mono text-[10px] text-[#F4F6FF]">SHA256SUMS.txt</code> on GitHub Releases to verify integrity.
        </div>
        <div class="flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-[#8E98B5]">
          <span>Includes bundled Tauri v2 WebView shell + standalone audio backend.</span>
          <a id="dl-releases-link" href="#download-section" target="_blank" rel="noopener noreferrer"
             class="font-mono text-[#F2F5FC] hover:underline inline-flex items-center gap-1" style="color: var(--accent);">
            <span>GitHub Releases ↗</span>
          </a>
        </div>
      </div>"""

OLD_RELEASE_JS = """    // Single place to update packaged Windows installer & GitHub release URLs
    const RELEASE_CONFIG = {
      exeUrl: './downloads/ORBIT_0.1.0_x64-setup.exe',
      msiUrl: './downloads/ORBIT_0.1.0_x64_en-US.msi',
      releasesUrl: 'https://github.com/naitikjain/orbit/releases/latest',
    };

    function hydrateReleaseLinks() {
      const exe = document.getElementById('dl-exe-link');
      const msi = document.getElementById('dl-msi-link');
      const rel = document.getElementById('dl-releases-link');
      if (exe) exe.href = RELEASE_CONFIG.exeUrl;
      if (msi) msi.href = RELEASE_CONFIG.msiUrl;
      if (rel) rel.href = RELEASE_CONFIG.releasesUrl;
    }"""

NEW_RELEASE_JS = """    // Live GitHub Releases & Cloudflare Pages Native "Save As" Downloader
    const GITHUB_OWNER = 'jainnaitk';
    const GITHUB_REPO = 'ORBIT';
    const GITHUB_BASE = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}`;
    const RELEASE_CONFIG = {
      githubRepo: `${GITHUB_OWNER}/${GITHUB_REPO}`,
      version: '0.1.0',
      exeName: 'ORBIT-Setup.exe',
      exeSize: 0,
      exeUrl: `${GITHUB_BASE}/releases/latest/download/ORBIT-Setup.exe`,
      msiName: '',
      msiUrl: '',
      releasesUrl: `${GITHUB_BASE}/releases/latest`,
    };

    function formatFileSize(bytes) {
      if (!bytes || bytes <= 0) return '';
      const mb = bytes / (1024 * 1024);
      return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
    }

    function updateReleaseUI() {
      const badge = document.getElementById('modal-version-badge');
      const nameEl = document.getElementById('dl-exe-filename');
      const metaEl = document.getElementById('dl-exe-meta');
      const rel = document.getElementById('dl-releases-link');
      const msiBtn = document.getElementById('dl-msi-btn');

      if (badge) badge.textContent = `Windows 10 / 11 • v${RELEASE_CONFIG.version}`;
      if (nameEl) nameEl.textContent = RELEASE_CONFIG.exeName;
      if (metaEl) {
        const sz = formatFileSize(RELEASE_CONFIG.exeSize);
        metaEl.textContent = `Windows 10/11 64-bit${sz ? ' • ' + sz : ''} • Native Tauri v2`;
      }
      if (rel) rel.href = RELEASE_CONFIG.releasesUrl;
      if (msiBtn) msiBtn.classList.toggle('hidden', !RELEASE_CONFIG.msiUrl);
    }

    async function hydrateReleaseLinks() {
      updateReleaseUI();
      try {
        const res = await fetch(`https://api.github.com/repos/${RELEASE_CONFIG.githubRepo}/releases/latest`, {
          headers: { Accept: 'application/vnd.github+json' },
        });
        if (!res.ok) return;
        const release = await res.json();
        const cleanVer = String(release.tag_name || '').replace(/^v/i, '').trim();
        if (cleanVer) RELEASE_CONFIG.version = cleanVer;
        if (release.html_url) RELEASE_CONFIG.releasesUrl = release.html_url;

        const assets = Array.isArray(release.assets) ? release.assets : [];
        const exeAsset =
          assets.find((a) => /setup\\.exe$/i.test(a.name || '') && !/^ORBIT-Setup\\.exe$/i.test(a.name || '')) ||
          assets.find((a) => /\\.exe$/i.test(a.name || ''));
        const msiAsset = assets.find((a) => /\\.msi$/i.test(a.name || ''));

        if (exeAsset && exeAsset.browser_download_url) {
          RELEASE_CONFIG.exeName = exeAsset.name;
          RELEASE_CONFIG.exeSize = Number(exeAsset.size || 0);
          RELEASE_CONFIG.exeUrl = exeAsset.browser_download_url;
        }
        if (msiAsset && msiAsset.browser_download_url) {
          RELEASE_CONFIG.msiName = msiAsset.name;
          RELEASE_CONFIG.msiUrl = msiAsset.browser_download_url;
        }
        updateReleaseUI();
      } catch {
        // Keep stable /releases/latest/download/ORBIT-Setup.exe fallback
      }
    }

    async function downloadOrbitAsset(kind = 'exe') {
      const isMsi = kind === 'msi' && Boolean(RELEASE_CONFIG.msiUrl);
      const filename = isMsi ? RELEASE_CONFIG.msiName : RELEASE_CONFIG.exeName;
      const directUrl = isMsi ? RELEASE_CONFIG.msiUrl : RELEASE_CONFIG.exeUrl;

      const wrap = document.getElementById('dl-progress-wrap');
      const txt = document.getElementById('dl-progress-text');
      const pctEl = document.getElementById('dl-progress-pct');
      const fill = document.getElementById('dl-progress-fill');
      const exeBtn = document.getElementById('dl-exe-btn');

      // Cloudflare Pages: Force native OS "Save As" explorer dialog on Chromium/Edge
      if (window.isSecureContext && typeof window.showSaveFilePicker === 'function' && window.location.protocol.startsWith('http')) {
        try {
          const ext = isMsi ? '.msi' : '.exe';
          const handle = await window.showSaveFilePicker({
            suggestedName: filename,
            types: [{
              description: isMsi ? 'Windows Installer Package' : 'Windows Installer',
              accept: { 'application/octet-stream': [ext] },
            }],
          });

          if (wrap) wrap.classList.remove('hidden');
          if (txt) txt.textContent = `Downloading ${filename}...`;
          if (pctEl) pctEl.textContent = '0%';
          if (fill) fill.style.transform = 'scaleX(0)';
          if (exeBtn) exeBtn.disabled = true;

          const proxyUrl = `/api/download?asset=${encodeURIComponent(filename)}`;
          const res = await fetch(proxyUrl);
          if (!res.ok || !res.body) throw new Error(`Proxy HTTP ${res.status}`);

          const total = Number(res.headers.get('content-length') || RELEASE_CONFIG.exeSize || 0);
          const writable = await handle.createWritable();
          const reader = res.body.getReader();
          let loaded = 0;

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            await writable.write(value);
            loaded += value.byteLength;
            if (total > 0) {
              const ratio = Math.min(1, loaded / total);
              if (pctEl) pctEl.textContent = `${Math.round(ratio * 100)}%`;
              if (fill) fill.style.transform = `scaleX(${ratio.toFixed(4)})`;
            }
          }
          await writable.close();
          if (txt) txt.textContent = `Saved ${filename}`;
          if (pctEl) pctEl.textContent = '100%';
          if (fill) fill.style.transform = 'scaleX(1)';
          showToast(`Saved ${filename} — double-click to install`);
          if (exeBtn) exeBtn.disabled = false;
          return;
        } catch (err) {
          if (exeBtn) exeBtn.disabled = false;
          if (wrap) wrap.classList.add('hidden');
          if (err && err.name === 'AbortError') return;
        }
      }

      // Fallback (Firefox / Safari / Local Preview): Direct attachment download without leaving page
      showToast(`Starting download: ${filename}`);
      const a = document.createElement('a');
      a.href = directUrl;
      a.download = filename;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }"""


def main() -> None:
    if not SRC.is_file():
        raise FileNotFoundError(f"Source showcase HTML not found: {SRC}")

    html = SRC.read_text(encoding="utf-8")

    replacements = [
        (OLD_MODAL_HEADER, NEW_MODAL_HEADER, "modal version badge"),
        (OLD_INSTALLER_PANEL, NEW_INSTALLER_PANEL, "installer panel"),
        (OLD_RELEASE_JS, NEW_RELEASE_JS, "release config & Save As downloader"),
    ]

    for old, new, label in replacements:
        if old not in html:
            raise ValueError(f"Could not find target block ({label}) in {SRC.name}")
        html = html.replace(old, new, 1)

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(html, encoding="utf-8")
    print(f"[BUILT] {DST.relative_to(ROOT)} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
