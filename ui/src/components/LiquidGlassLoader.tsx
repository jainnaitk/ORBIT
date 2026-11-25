import { useEffect, useRef } from 'react';

// Precomputed 4096-entry trigonometric LUTs for fast ~2ms CPU liquid-glass normal shading
// (works 100% natively in Tauri even with --disable-3d-apis / WebGL disabled)
const LUT_SIZE = 4096;
const LUT_MASK = LUT_SIZE - 1;
const RAD_TO_LUT = LUT_SIZE / (Math.PI * 2);
const SIN_LUT = new Float32Array(LUT_SIZE);
const COS_LUT = new Float32Array(LUT_SIZE);

for (let i = 0; i < LUT_SIZE; i++) {
  const rad = (i / LUT_SIZE) * Math.PI * 2;
  SIN_LUT[i] = Math.sin(rad);
  COS_LUT[i] = Math.cos(rad);
}

function fastSin(x: number): number {
  return SIN_LUT[Math.floor(x * RAD_TO_LUT) & LUT_MASK];
}

function fastCos(x: number): number {
  return COS_LUT[Math.floor(x * RAD_TO_LUT) & LUT_MASK];
}

function parseRgbVar(name: string, fallback: [number, number, number]): [number, number, number] {
  try {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const parts = raw.split(',').map((s) => Number(s.trim()));
    if (parts.length === 3 && parts.every((n) => Number.isFinite(n))) {
      return [parts[0] / 255, parts[1] / 255, parts[2] / 255];
    }
  } catch {
    // Fallback
  }
  return fallback;
}

// Half-vectors for dual glossy studio lights (V = (0, 0, 1))
// Key light L1 = norm(0.54, -0.60, 0.59) -> H1 = norm(L1 + V)
const H1X = 0.305;
const H1Y = -0.339;
const H1Z = 0.890;

// Rim / fill light L2 = norm(-0.66, 0.48, 0.58) -> H2 = norm(L2 + V)
const H2X = -0.371;
const H2Y = 0.270;
const H2Z = 0.888;

// Soft tertiary grazing reflection L3 = norm(0.18, 0.72, 0.67) -> H3 = norm(L3 + V)
const H3X = 0.099;
const H3Y = 0.394;
const H3Z = 0.914;

export const LiquidGlassLoader = () => {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    const displayCanvas = canvasRef.current;
    if (!host || !displayCanvas) return;

    const dctx = displayCanvas.getContext('2d', { alpha: false });
    if (!dctx) return;

    const offCanvas = document.createElement('canvas');
    const octx = offCanvas.getContext('2d', { alpha: false });
    if (!octx) return;

    let simW = 136;
    let simH = 180;
    let heightBuf = new Float32Array(simW * simH);
    let imgData = octx.createImageData(simW, simH);

    const resize = () => {
      const rect = host.getBoundingClientRect();
      const cssW = Math.max(120, Math.round(rect.width));
      const cssH = Math.max(120, Math.round(rect.height));
      const aspect = cssH / cssW;

      // Render display canvas at crisp half-to-native res for buttery bilinear upscaling
      displayCanvas.width = Math.min(540, Math.max(220, Math.round(cssW * 0.75)));
      displayCanvas.height = Math.min(720, Math.max(220, Math.round(cssH * 0.75)));

      // Compact internal fluid simulation grid (~22k–28k pixels -> ~2ms/frame on i3)
      simW = aspect > 1.1 ? 132 : 164;
      simH = Math.max(112, Math.min(216, Math.round(simW * aspect)));
      offCanvas.width = simW;
      offCanvas.height = simH;
      heightBuf = new Float32Array(simW * simH);
      imgData = octx.createImageData(simW, simH);
    };

    resize();

    // Live-synced Accent Theme & Cosmic Effects state (tracks song changes in real time)
    const curAcc: [number, number, number] = parseRgbVar('--dynamic-accent-rgb', [
      45 / 255,
      212 / 255,
      191 / 255,
    ]);
    const curSec: [number, number, number] = parseRgbVar('--dynamic-secondary-rgb', [
      99 / 255,
      102 / 255,
      241 / 255,
    ]);
    let targetAcc: [number, number, number] = [curAcc[0], curAcc[1], curAcc[2]];
    let targetSec: [number, number, number] = [curSec[0], curSec[1], curSec[2]];
    let effectsMode = document.documentElement.getAttribute('data-effects') || 'full';
    let isOledMode = effectsMode === 'oled';
    let isStaticMode = effectsMode === 'static' || isOledMode;

    // Randomized starting phase so each track load starts with a fresh organic wave shape
    const seedOffset = Math.random() * 40;
    let rafId = 0;
    let startAt = performance.now();
    let lastDrawAt = 0;
    let lastColorPollAt = 0;
    let lastFrameSec = 0;

    const renderFrame = (timeSec: number, dtSec: number) => {
      lastFrameSec = timeSec;
      const t = timeSec + seedOffset;
      const w = simW;
      const h = simH;
      const invMin = 2.65 / Math.min(w, h);
      const halfW = w * 0.5;
      const halfH = h * 0.5;

      // Fast, fluid palette sync whenever active song's dynamic accent theme changes
      const colorAlpha = isStaticMode ? 1.0 : Math.min(1.0, Math.max(0.14, dtSec * 11.0));
      for (let i = 0; i < 3; i++) {
        curAcc[i] += (targetAcc[i] - curAcc[i]) * colorAlpha;
        curSec[i] += (targetSec[i] - curSec[i]) * colorAlpha;
      }

      const ar = curAcc[0];
      const ag = curAcc[1];
      const ab = curAcc[2];
      const sr = curSec[0];
      const sg = curSec[1];
      const sb = curSec[2];

      const t1 = t * 0.58;
      const t2 = t * 0.43;
      const t3 = t * 0.51;
      const t4 = t * 0.37;
      const t5 = t * 0.29;
      const tPhase = t * 0.34;

      // Pass 1: 2-stage domain-warped liquid heightfield (sculpted smoked-glass folds)
      let p = 0;
      for (let y = 0; y < h; y++) {
        const uy = (y - halfH) * invMin;
        for (let x = 0; x < w; x++) {
          const ux = (x - halfW) * invMin;

          // First fluid advection warp
          const qx =
            ux +
            0.54 * fastSin(uy * 1.55 + t1) +
            0.30 * fastCos(ux * 1.12 - uy * 1.28 - t2);
          const qy =
            uy +
            0.54 * fastCos(ux * 1.48 - t3) +
            0.30 * fastSin(uy * 1.18 + ux * 1.05 + t4);

          // Second curl fold (organic swirling smoked-glass ribbons)
          const rx = qx + 0.46 * fastSin(qy * 2.1 - qx * 0.78 + t2 * 1.15);
          const ry = qy + 0.46 * fastCos(qx * 1.98 + qy * 0.82 - t3 * 1.08);

          // Layered height with folded glass ridge crests
          const f1 = fastSin(rx * 1.75 + ry * 1.18 + t5);
          const f2 = fastCos(rx * 1.08 - ry * 1.92 - t4 * 0.85);
          const f3 = fastSin((rx - ry) * 1.48 + 0.62 * f1 - t5 * 0.72);
          const crest = 1.0 - Math.abs(f3);

          heightBuf[p++] = 0.46 * f1 + 0.34 * f2 + 0.52 * (crest * crest);
        }
      }

      // Pass 2: Theme-synced dark obsidian background + refracted accent waves
      const pixels = imgData.data;
      const wMinus1 = w - 1;
      const hMinus1 = h - 1;
      const invW = 1.0 / wMinus1;
      const invH = 1.0 / hMinus1;
      const bgGlowScale = isOledMode ? 0.24 : 1.0;
      let out = 0;

      for (let y = 0; y < h; y++) {
        const ym = y > 0 ? y - 1 : 0;
        const yp = y < hMinus1 ? y + 1 : hMinus1;
        const row = y * w;
        const rowM = ym * w;
        const rowP = yp * w;

        const nyNorm = y * invH;
        const vy = nyNorm * 2.0 - 1.0;

        // Base vertical obsidian gradient matching .ios-liquid-drawer (#0C101C -> #060912)
        const baseR = isOledMode ? 0.008 : 0.044 - nyNorm * 0.020;
        const baseG = isOledMode ? 0.010 : 0.059 - nyNorm * 0.024;
        const baseB = isOledMode ? 0.016 : 0.102 - nyNorm * 0.036;

        for (let x = 0; x < w; x++) {
          const xm = x > 0 ? x - 1 : 0;
          const xp = x < wMinus1 ? x + 1 : wMinus1;

          const hVal = heightBuf[row + x];
          const dx = (heightBuf[row + xp] - heightBuf[row + xm]) * 3.0;
          const dy = (heightBuf[rowP + x] - heightBuf[rowM + x]) * 3.0;

          const invLen = 1.0 / Math.sqrt(dx * dx + dy * dy + 1.0);
          const nx = -dx * invLen;
          const ny = -dy * invLen;
          const nz = invLen;

          // Refract ORBIT's top-left Accent & bottom-right Secondary ambient background glows
          // directly through the curved liquid-glass normals
          const nxNorm = x * invW;
          const rxCoord = nxNorm + nx * 0.16;
          const ryCoord = nyNorm + ny * 0.16;

          const dAccX = (rxCoord - 0.16) * 1.18;
          const dAccY = (ryCoord - 0.14) * 1.25;
          const accDistSq = dAccX * dAccX + dAccY * dAccY;
          const bgAcc = accDistSq < 1.0 ? (1.0 - accDistSq) * (1.0 - accDistSq) * 0.17 * bgGlowScale : 0.0;

          const dSecX = (rxCoord - 0.84) * 1.22;
          const dSecY = (ryCoord - 0.86) * 1.28;
          const secDistSq = dSecX * dSecX + dSecY * dSecY;
          const bgSec = secDistSq < 1.0 ? (1.0 - secDistSq) * (1.0 - secDistSq) * 0.15 * bgGlowScale : 0.0;

          // Grazing Fresnel edge reflection along curved dark-glass folds
          const oneMinusNz = 1.0 - nz;
          const fresnel =
            oneMinusNz * oneMinusNz * oneMinusNz * 1.4 + oneMinusNz * oneMinusNz * 0.28;

          // Specular 1: crisp smoked-glass ridge glint (ndoth1^32)
          let s1 = nx * H1X + ny * H1Y + nz * H1Z;
          if (s1 < 0) s1 = 0;
          const s1_2 = s1 * s1;
          const s1_4 = s1_2 * s1_2;
          const s1_8 = s1_4 * s1_4;
          const s1_16 = s1_8 * s1_8;
          const specSharp = s1_16 * s1_16;

          // Specular 2: soft accent-tinted studio sheen (ndoth2^12)
          let s2 = nx * H2X + ny * H2Y + nz * H2Z;
          if (s2 < 0) s2 = 0;
          const s2_2 = s2 * s2;
          const s2_4 = s2_2 * s2_2;
          const specSoft = s2_4 * s2_4 * s2_4;

          // Specular 3: faint secondary liquid bounce (ndoth3^8)
          let s3 = nx * H3X + ny * H3Y + nz * H3Z;
          if (s3 < 0) s3 = 0;
          const s3_2 = s3 * s3;
          const s3_4 = s3_2 * s3_2;
          const specFill = s3_4 * s3_4;

          // Flowing wave color blend anchored to active song's Accent & Secondary background colors
          const env = nx * 1.9 + ny * 1.65 + hVal * 1.28;
          const tintMix = 0.5 + 0.5 * fastCos(env * 1.75 + tPhase);
          const tintR = ar * tintMix + sr * (1.0 - tintMix);
          const tintG = ag * tintMix + sg * (1.0 - tintMix);
          const tintB = ab * tintMix + sb * (1.0 - tintMix);

          // Subtle cool glass-white pearl tinted lightly by active accent
          const pearlR = 0.72 + 0.24 * tintR;
          const pearlG = 0.74 + 0.22 * tintG;
          const pearlB = 0.79 + 0.20 * tintB;

          // Deep dark troughs matching panel background, with clearly readable accent-infused smoked-glass ridges
          const ridge = Math.max(0.0, Math.min(1.0, (hVal + 0.38) * 0.62));
          const ridgeSq = ridge * ridge;
          const waveTint = (0.105 * ridgeSq + 0.042 * ridge) * (isOledMode ? 0.55 : 1.0);
          const rimGlow = fresnel * (0.22 + 0.16 * ridge) * (isOledMode ? 0.65 : 1.0);

          const bgR = baseR + bgAcc * ar + bgSec * sr;
          const bgG = baseG + bgAcc * ag + bgSec * sg;
          const bgB = baseB + bgAcc * ab + bgSec * sb;

          let r =
            bgR +
            waveTint * (0.24 + 0.76 * tintR) +
            rimGlow * (0.76 * tintR + 0.24 * pearlR) +
            specSoft * (0.24 * tintR + 0.09 * pearlR) +
            specFill * (0.16 * sr + 0.07 * pearlR) +
            specSharp * (0.36 * pearlR + 0.26 * ar);
          let g =
            bgG +
            waveTint * (0.26 + 0.74 * tintG) +
            rimGlow * (0.76 * tintG + 0.24 * pearlG) +
            specSoft * (0.24 * tintG + 0.09 * pearlG) +
            specFill * (0.16 * sg + 0.07 * pearlG) +
            specSharp * (0.37 * pearlG + 0.26 * ag);
          let b =
            bgB +
            waveTint * (0.30 + 0.70 * tintB) +
            rimGlow * (0.76 * tintB + 0.24 * pearlB) +
            specSoft * (0.25 * tintB + 0.10 * pearlB) +
            specFill * (0.17 * sb + 0.08 * pearlB) +
            specSharp * (0.40 * pearlB + 0.26 * ab);

          // Gentle outer blend so wave crests soften gracefully toward the drawer rim
          const vx = nxNorm * 2.0 - 1.0;
          const edgeFade = Math.max(0.45, 1.0 - (vx * vx * 0.26 + vy * vy * 0.22));
          r = bgR + (r - bgR) * edgeFade;
          g = bgG + (g - bgG) * edgeFade;
          b = bgB + (b - bgB) * edgeFade;

          pixels[out++] = Math.min(255, Math.max(0, (r * 255) | 0));
          pixels[out++] = Math.min(255, Math.max(0, (g * 255) | 0));
          pixels[out++] = Math.min(255, Math.max(0, (b * 255) | 0));
          pixels[out++] = 255;
        }
      }

      octx.putImageData(imgData, 0, 0);
      dctx.imageSmoothingEnabled = true;
      dctx.imageSmoothingQuality = 'high';
      dctx.drawImage(offCanvas, 0, 0, displayCanvas.width, displayCanvas.height);
    };

    const tick = (now: number) => {
      if (document.hidden || isStaticMode) {
        rafId = 0;
        return;
      }
      // Poll root accent CSS vars every ~160ms as safety net alongside MutationObserver
      if (now - lastColorPollAt >= 160) {
        lastColorPollAt = now;
        targetAcc = parseRgbVar('--dynamic-accent-rgb', targetAcc);
        targetSec = parseRgbVar('--dynamic-secondary-rgb', targetSec);
      }
      // Smooth ~36fps cap keeps fluid motion silky while keeping CPU usage minimal
      if (now - lastDrawAt >= 26) {
        const dtSec = lastDrawAt > 0 ? Math.min(0.08, (now - lastDrawAt) / 1000) : 0.028;
        lastDrawAt = now;
        renderFrame((now - startAt) / 1000, dtSec);
      }
      rafId = requestAnimationFrame(tick);
    };

    const startLoop = () => {
      if (rafId || document.hidden || isStaticMode) return;
      lastDrawAt = 0;
      rafId = requestAnimationFrame(tick);
    };

    const stopLoop = () => {
      if (rafId) {
        cancelAnimationFrame(rafId);
        rafId = 0;
      }
    };

    const syncThemeFromRoot = () => {
      targetAcc = parseRgbVar('--dynamic-accent-rgb', targetAcc);
      targetSec = parseRgbVar('--dynamic-secondary-rgb', targetSec);
      effectsMode = document.documentElement.getAttribute('data-effects') || 'full';
      isOledMode = effectsMode === 'oled';
      isStaticMode = effectsMode === 'static' || isOledMode;

      if (isStaticMode) {
        stopLoop();
        renderFrame(lastFrameSec, 1.0);
      } else {
        startLoop();
      }
    };

    const ro =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(() => {
            resize();
            renderFrame(lastFrameSec, 0.016);
          })
        : null;
    ro?.observe(host);

    // Watch root CSS variables & data-effects so song accent changes sync instantaneously
    const mo =
      typeof MutationObserver !== 'undefined'
        ? new MutationObserver(() => {
            syncThemeFromRoot();
          })
        : null;
    mo?.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['style', 'data-effects'],
    });

    const onVisibility = () => {
      if (document.hidden) {
        stopLoop();
      } else if (!isStaticMode) {
        startAt = performance.now() - lastFrameSec * 1000;
        startLoop();
      }
    };

    // Always draw first frame immediately (zero blank flash)
    renderFrame(0, 1.0);
    if (!isStaticMode) startLoop();

    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stopLoop();
      document.removeEventListener('visibilitychange', onVisibility);
      mo?.disconnect();
      ro?.disconnect();
    };
  }, []);

  return (
    <div
      ref={hostRef}
      role="status"
      aria-label="Syncing lyrics"
      className="eh-liquid-stage-loader absolute inset-0 overflow-hidden flex items-center justify-center"
    >
      {/* Real-time 3D domain-warped iridescent liquid glass fluid canvas */}
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="eh-liquid-canvas absolute -inset-2 w-[calc(100%+16px)] h-[calc(100%+16px)] object-cover"
      />

      {/* Optical glass depth & soft dark edge diffusion so it sits seamlessly in ORBIT */}
      <span className="eh-liquid-glass-glaze" aria-hidden="true" />

      {/* Minimal floating refractive glass status capsule */}
      <div className="eh-liquid-status-pill relative z-10 px-4 py-2 rounded-full flex items-center gap-2">
        <span
          className="w-1.5 h-1.5 rounded-full animate-pulse"
          style={{
            background: 'var(--dynamic-accent)',
            boxShadow: '0 0 10px var(--dynamic-accent)',
          }}
        />
        <span className="text-[11px] font-semibold tracking-[0.14em] uppercase text-white/95">
          Syncing lyrics...
        </span>
      </div>
    </div>
  );
};
