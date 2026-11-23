/**
 * Extracts a WCAG-safe cosmic accent from active track artwork using a 12x12 canvas.
 * Falls back cleanly to deterministic Event Horizon accents if CORS blocks canvas reads.
 */

export interface CosmicAccent {
  hex: string;
  rgb: string;
  secondaryHex: string;
  secondaryRgb: string;
}

const DEFAULT_PALETTES: CosmicAccent[] = [
  { hex: '#2DD4BF', rgb: '45, 212, 191', secondaryHex: '#6366F1', secondaryRgb: '99, 102, 241' },
  { hex: '#38BDF8', rgb: '56, 189, 248', secondaryHex: '#8B5CF6', secondaryRgb: '139, 92, 246' },
  { hex: '#FB7185', rgb: '251, 113, 133', secondaryHex: '#F59E0B', secondaryRgb: '245, 158, 11' },
  { hex: '#A78BFA', rgb: '167, 139, 250', secondaryHex: '#EC4899', secondaryRgb: '236, 72, 153' },
  { hex: '#34D399', rgb: '52, 211, 153', secondaryHex: '#0EA5E9', secondaryRgb: '14, 165, 233' },
  { hex: '#FBBF24', rgb: '251, 191, 36', secondaryHex: '#F43F5E', secondaryRgb: '244, 63, 94' },
  { hex: '#F472B6', rgb: '244, 114, 182', secondaryHex: '#6366F1', secondaryRgb: '99, 102, 241' },
  { hex: '#FB923C', rgb: '251, 146, 60', secondaryHex: '#A855F7', secondaryRgb: '168, 85, 247' },
];

export const DEFAULT_COSMIC_ACCENT = DEFAULT_PALETTES[0];

const accentCache = new Map<string, CosmicAccent>();

function hashFallback(seed: string): CosmicAccent {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (h * 31 + seed.charCodeAt(i)) | 0;
  }
  return DEFAULT_PALETTES[Math.abs(h) % DEFAULT_PALETTES.length];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const hue2rgb = (p: number, q: number, t: number) => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [
    Math.round(hue2rgb(p, q, h + 1 / 3) * 255),
    Math.round(hue2rgb(p, q, h) * 255),
    Math.round(hue2rgb(p, q, h - 1 / 3) * 255),
  ];
}

function deriveCosmicPair(
  r: number,
  g: number,
  b: number
): { primary: [number, number, number]; secondary: [number, number, number] } {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  let h = 0;
  let s = 0;
  let l = (max + min) / 2;

  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case rn:
        h = (gn - bn) / d + (gn < bn ? 6 : 0);
        break;
      case gn:
        h = (bn - rn) / d + 2;
        break;
      case bn:
        h = (rn - gn) / d + 4;
        break;
    }
    h /= 6;
  }

  // Vivid, unmistakable cosmic accent that pops cleanly on dark obsidian surfaces
  const primaryS = Math.max(0.68, Math.min(0.94, s * 1.15));
  const primaryL = Math.max(0.56, Math.min(0.68, l));
  const secondaryH = (h + 0.14) % 1; // +50deg harmonious companion hue
  const secondaryS = Math.max(0.62, Math.min(0.88, primaryS - 0.06));
  const secondaryL = 0.54;

  return {
    primary: hslToRgb(h, primaryS, primaryL),
    secondary: hslToRgb(secondaryH, secondaryS, secondaryL),
  };
}

function rgbToHex(r: number, g: number, b: number): string {
  return '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');
}

function downscaleThumbForSampling(url: string): string {
  const trimmed = url.trim();
  if (trimmed.includes('googleusercontent.com') || trimmed.includes('ggpht.com')) {
    if (/=w\d+-h\d+/.test(trimmed)) {
      return trimmed.replace(/=w\d+-h\d+/, '=w64-h64');
    }
  }
  return trimmed;
}

export async function extractTrackAccent(
  videoId: string,
  thumbnailUrl?: string | null
): Promise<CosmicAccent> {
  const cached = accentCache.get(videoId);
  if (cached) return cached;

  const fallback = hashFallback(videoId);
  if (!thumbnailUrl) {
    accentCache.set(videoId, fallback);
    return fallback;
  }

  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.referrerPolicy = 'no-referrer';
    img.decoding = 'async';

    const finish = (result: CosmicAccent) => {
      img.onload = null;
      img.onerror = null;
      if (accentCache.size > 48) {
        const firstKey = accentCache.keys().next().value;
        if (firstKey) accentCache.delete(firstKey);
      }
      accentCache.set(videoId, result);
      resolve(result);
    };

    img.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = 12;
        canvas.height = 12;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        if (!ctx) return finish(fallback);
        ctx.drawImage(img, 0, 0, 12, 12);
        const data = ctx.getImageData(0, 0, 12, 12).data;

        let bestR = 0;
        let bestG = 0;
        let bestB = 0;
        let bestScore = -1;

        for (let i = 0; i < data.length; i += 4) {
          const r = data[i];
          const g = data[i + 1];
          const b = data[i + 2];
          const max = Math.max(r, g, b);
          const min = Math.min(r, g, b);
          const sat = max === 0 ? 0 : (max - min) / max;
          const lum = (max + min) / 510;
          const score = sat * 1.4 - Math.abs(lum - 0.52);
          if (score > bestScore && max > 40) {
            bestScore = score;
            bestR = r;
            bestG = g;
            bestB = b;
          }
        }

        if (bestScore < 0.08) return finish(fallback);

        const { primary: [cr, cg, cb], secondary: [sr, sg, sb] } = deriveCosmicPair(bestR, bestG, bestB);
        finish({
          hex: rgbToHex(cr, cg, cb),
          rgb: `${cr}, ${cg}, ${cb}`,
          secondaryHex: rgbToHex(sr, sg, sb),
          secondaryRgb: `${sr}, ${sg}, ${sb}`,
        });
      } catch {
        finish(fallback);
      }
    };

    img.onerror = () => finish(fallback);
    img.src = downscaleThumbForSampling(thumbnailUrl);
  });
}
