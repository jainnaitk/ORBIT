import { memo, useEffect, useRef } from 'react';

export type CosmicEffectsMode = 'full' | 'static' | 'oled';

interface StarfieldBgProps {
  mode: CosmicEffectsMode;
  absorbPulseKey?: number;
  isPlaying?: boolean;
  paused?: boolean;
}

interface DiskParticle {
  r: number;
  angle: number;
  speed: number;
  y: number;
  size: number;
  t: number;
}

interface Star {
  x: number;
  y: number;
  r: number;
  alpha: number;
  speed: number;
  phase: number;
  depth: number;
}

function parseRgbVar(name: string, fallback: [number, number, number]): [number, number, number] {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const parts = raw.split(',').map((v) => Number(v.trim()));
  if (parts.length === 3 && parts.every((n) => !Number.isNaN(n))) {
    return [parts[0], parts[1], parts[2]];
  }
  return fallback;
}

export const StarfieldBg = memo(({
  mode,
  absorbPulseKey = 0,
  isPlaying = true,
  paused = false,
}: StarfieldBgProps) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const shockRef = useRef<number>(1.0);
  const isPlayingRef = useRef<boolean>(isPlaying);
  const pausedRef = useRef<boolean>(paused);
  const syncLoopRef = useRef<(() => void) | null>(null);
  isPlayingRef.current = isPlaying;
  pausedRef.current = paused;

  useEffect(() => {
    syncLoopRef.current?.();
  }, [paused]);

  useEffect(() => {
    if (absorbPulseKey > 0) shockRef.current = 0.0;
  }, [absorbPulseKey]);

  useEffect(() => {
    if (mode === 'oled') return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return;

    let width = window.innerWidth;
    let height = window.innerHeight;

    // Cap backing store at 960px wide (dpr=1) -> ~44% less VRAM & fill-rate (<2.1MB framebuffer)
    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      const scaleFactor = Math.min(1, 960 / Math.max(1, width));
      canvas.width = Math.max(320, Math.round(width * scaleFactor));
      canvas.height = Math.max(200, Math.round(height * scaleFactor));
      ctx.setTransform(
        canvas.width / Math.max(1, width),
        0,
        0,
        canvas.height / Math.max(1, height),
        0,
        0
      );
    };
    resize();

    // 120 crisp small stars — clear through frosted glass with minimal CPU cost
    const stars: Star[] = Array.from({ length: 120 }, (_, i) => {
      const depth = i % 3 === 0 ? 1.0 : i % 3 === 1 ? 0.62 : 0.35;
      return {
        x: Math.random() * width,
        y: Math.random() * height,
        r: depth > 0.8 ? 1.45 : depth > 0.5 ? 1.05 : 0.75,
        alpha: 0.32 + Math.random() * 0.56,
        speed: 0.0009 + Math.random() * 0.0015,
        phase: Math.random() * Math.PI * 2,
        depth,
      };
    });

    // 180 Keplerian Accretion Disk Particles + pre-allocated typed arrays (zero per-frame GC)
    const PARTICLE_COUNT = 180;
    const particles: DiskParticle[] = Array.from({ length: PARTICLE_COUNT }, () => {
      const t = Math.pow(Math.random(), 1.5);
      const r = 82 + t * 170;
      return {
        r,
        angle: Math.random() * Math.PI * 2,
        speed: (0.82 / Math.pow(r / 82, 1.35)) * (0.85 + Math.random() * 0.3),
        y: (Math.random() - 0.5) * (3 + t * 9.5),
        size: (1.35 + Math.random() * 1.25) * (1.0 - t * 0.35),
        t,
      };
    });

    const projX = new Float32Array(PARTICLE_COUNT);
    const projY = new Float32Array(PARTICLE_COUNT);
    const projRad = new Float32Array(PARTICLE_COUNT);
    const projBucket = new Uint8Array(PARTICLE_COUNT);

    const STAR_STYLES = [
      'rgba(225, 236, 255, 0.32)',
      'rgba(235, 243, 255, 0.58)',
      'rgba(248, 251, 255, 0.88)',
    ];

    let acc = parseRgbVar('--dynamic-accent-rgb', [45, 212, 191]);
    let sec = parseRgbVar('--dynamic-secondary-rgb', [99, 102, 241]);
    let targetAcc = [...acc] as [number, number, number];
    let targetSec = [...sec] as [number, number, number];

    let mx = 0;
    let my = 0;
    let smx = 0;
    let smy = 0;
    let simTime = 0;
    let lastColorPoll = 0;

    const onMove = (e: MouseEvent) => {
      mx = e.clientX / Math.max(1, width) - 0.5;
      my = e.clientY / Math.max(1, height) - 0.5;
    };

    const tiltX = 0.29;
    const tiltZ = -0.21;
    const cosX = Math.cos(tiltX);
    const sinX = Math.sin(tiltX);
    const cosZ = Math.cos(tiltZ);
    const sinZ = Math.sin(tiltZ);

    // Cached per-color / per-geometry canvas styles (zero per-frame allocations when steady)
    let cachedColorKey = '';
    let photonArcStyle = '';
    let rearStyles: string[] = [];
    let frontStyles: string[] = [];
    let cachedRimKey = '';
    let cachedRimGrad: CanvasGradient | null = null;
    let cachedArcKey = '';
    let cachedArcGrad: CanvasGradient | null = null;

    const drawFrame = (dt: number, now: number) => {
      if (now - lastColorPoll > 300) {
        lastColorPoll = now;
        targetAcc = parseRgbVar('--dynamic-accent-rgb', targetAcc);
        targetSec = parseRgbVar('--dynamic-secondary-rgb', targetSec);
      }

      const cLerp = mode === 'static' ? 1 : Math.min(1, dt * 4.2);
      for (let i = 0; i < 3; i++) {
        acc[i] += (targetAcc[i] - acc[i]) * cLerp;
        sec[i] += (targetSec[i] - sec[i]) * cLerp;
      }

      smx += (mx - smx) * Math.min(1, dt * 4.2);
      smy += (my - smy) * Math.min(1, dt * 4.2);
      if (mode === 'full') {
        simTime += dt * (isPlayingRef.current ? 0.88 : 0.24);
      }

      const ar = Math.round(acc[0]), ag = Math.round(acc[1]), ab = Math.round(acc[2]);
      const sr = Math.round(sec[0]), sg = Math.round(sec[1]), sb = Math.round(sec[2]);

      const cx = width * 0.72 - smx * 14;
      const cy = height * 0.29 - smy * 10;
      const sphereR = Math.min(width, height) * 0.112;
      const scale = sphereR / 78;

      const colorKey = `${ar},${ag},${ab}|${sr},${sg},${sb}`;
      if (colorKey !== cachedColorKey) {
        cachedColorKey = colorKey;
        const mr = Math.round((ar + sr) * 0.5);
        const mg = Math.round((ag + sg) * 0.5);
        const mb = Math.round((ab + sb) * 0.5);
        photonArcStyle = `rgba(${ar}, ${ag}, ${ab}, 0.36)`;
        rearStyles = [
          'rgba(240, 246, 255, 0.34)',
          `rgba(${ar}, ${ag}, ${ab}, 0.34)`,
          `rgba(${mr}, ${mg}, ${mb}, 0.24)`,
          `rgba(${sr}, ${sg}, ${sb}, 0.16)`,
        ];
        frontStyles = [
          'rgba(248, 251, 255, 0.62)',
          `rgba(${ar}, ${ag}, ${ab}, 0.56)`,
          `rgba(${mr}, ${mg}, ${mb}, 0.40)`,
          `rgba(${sr}, ${sg}, ${sb}, 0.28)`,
        ];
      }

      // 1. Pure Deep Cosmic Void (NO big circular glow / fog clouds)
      ctx.fillStyle = '#020308';
      ctx.fillRect(0, 0, width, height);

      // 2. Crisp Sparkling Small Starfield
      for (let tier = 0; tier < 3; tier++) {
        ctx.fillStyle = STAR_STYLES[tier];
        for (let i = 0; i < stars.length; i++) {
          const s = stars[i];
          if (tier === 0 && mode === 'full') {
            s.y -= s.depth * 0.08;
            if (s.y < -3) {
              s.y = height + 3;
              s.x = Math.random() * width;
            }
          }
          const tw = mode === 'full' ? 0.62 + 0.38 * Math.sin(now * s.speed + s.phase) : 1;
          const a = s.alpha * tw;
          const sTier = a < 0.44 ? 0 : a < 0.68 ? 1 : 2;
          if (sTier !== tier) continue;
          const px = s.x - smx * s.depth * 12;
          const py = s.y - smy * s.depth * 12;
          ctx.fillRect(px, py, s.r, s.r);
        }
      }

      // 3. Gravitationally Lensed Upper Photon Arc (behind Event Horizon)
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(tiltZ);
      ctx.strokeStyle = photonArcStyle;
      ctx.lineWidth = 3.2 * scale;
      ctx.beginPath();
      ctx.ellipse(0, -sphereR * 0.04, sphereR * 1.21, sphereR * 1.12, 0, Math.PI, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = 'rgba(244, 248, 255, 0.28)';
      ctx.lineWidth = 1.2 * scale;
      ctx.beginPath();
      ctx.ellipse(0, -sphereR * 0.02, sphereR * 1.16, sphereR * 1.07, 0, Math.PI, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      // Pre-project disk particles into typed arrays (0 heap allocations)
      for (let i = 0; i < PARTICLE_COUNT; i++) {
        const p = particles[i];
        const ang = p.angle + simTime * p.speed;
        const x0 = Math.cos(ang) * p.r * scale;
        const z0 = Math.sin(ang) * p.r * scale;
        const y0 = p.y * scale;

        const y1 = y0 * cosX - z0 * sinX;
        const z1 = y0 * sinX + z0 * cosX;

        const x2 = x0 * cosZ - y1 * sinZ;
        const y2 = x0 * sinZ + y1 * cosZ;

        const persp = 680 / (680 + z1);
        projX[i] = cx + x2 * persp;
        projY[i] = cy + y2 * persp;
        projRad[i] = Math.max(0.8, p.size * scale * persp);

        const colorTier = p.t < 0.2 ? 0 : p.t < 0.5 ? 1 : p.t < 0.8 ? 2 : 3;
        projBucket[i] = (z1 >= 0 ? 4 : 0) | colorTier;
      }

      // 4. Rear Accretion Disk Particles
      for (let tier = 0; tier < 4; tier++) {
        ctx.fillStyle = rearStyles[tier];
        for (let i = 0; i < PARTICLE_COUNT; i++) {
          if (projBucket[i] !== tier) continue;
          const r = projRad[i];
          ctx.fillRect(projX[i] - r, projY[i] - r, r * 1.8, r * 1.8);
        }
      }

      // 5. Pitch-Black Event Horizon Sphere + Razor-Tight Fresnel Photon Rim
      ctx.fillStyle = '#000000';
      ctx.beginPath();
      ctx.arc(cx, cy, sphereR, 0, Math.PI * 2);
      ctx.fill();

      const rimKey = `${Math.round(cx)},${Math.round(cy)},${Math.round(sphereR)}|${ar},${ag},${ab}`;
      if (rimKey !== cachedRimKey || !cachedRimGrad) {
        cachedRimKey = rimKey;
        const rimGrad = ctx.createRadialGradient(cx, cy, sphereR * 0.90, cx, cy, sphereR * 1.05);
        rimGrad.addColorStop(0, 'rgba(0,0,0,0)');
        rimGrad.addColorStop(0.68, `rgba(${ar}, ${ag}, ${ab}, 0.50)`);
        rimGrad.addColorStop(0.90, 'rgba(244, 248, 255, 0.64)');
        rimGrad.addColorStop(1, 'rgba(0,0,0,0)');
        cachedRimGrad = rimGrad;
      }
      ctx.fillStyle = cachedRimGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, sphereR * 1.05, 0, Math.PI * 2);
      ctx.fill();

      // 6. Front Accretion Disk Arc
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(tiltZ);
      const arcKey = `${Math.round(sphereR)}|${colorKey}`;
      if (arcKey !== cachedArcKey || !cachedArcGrad) {
        cachedArcKey = arcKey;
        const arcGrad = ctx.createLinearGradient(-sphereR * 2.55, 0, sphereR * 2.55, 0);
        arcGrad.addColorStop(0, `rgba(${sr}, ${sg}, ${sb}, 0)`);
        arcGrad.addColorStop(0.22, `rgba(${ar}, ${ag}, ${ab}, 0.52)`);
        arcGrad.addColorStop(0.5, 'rgba(248, 250, 255, 0.72)');
        arcGrad.addColorStop(0.78, `rgba(${ar}, ${ag}, ${ab}, 0.52)`);
        arcGrad.addColorStop(1, `rgba(${sr}, ${sg}, ${sb}, 0)`);
        cachedArcGrad = arcGrad;
      }
      ctx.strokeStyle = cachedArcGrad;
      ctx.lineWidth = 2.6 * scale;
      ctx.beginPath();
      ctx.ellipse(0, 0, sphereR * 2.55, sphereR * 0.40, 0, 0.08, Math.PI - 0.08);
      ctx.stroke();
      ctx.restore();

      for (let tier = 0; tier < 4; tier++) {
        ctx.fillStyle = frontStyles[tier];
        const mask = 4 | tier;
        for (let i = 0; i < PARTICLE_COUNT; i++) {
          if (projBucket[i] !== mask) continue;
          const r = projRad[i];
          ctx.fillRect(projX[i] - r, projY[i] - r, r * 1.8, r * 1.8);
        }
      }

      // 7. Crisp Hairline Shockwave Ring on Song Change (no wide haze)
      if (shockRef.current < 1.0) {
        shockRef.current = Math.min(1.0, shockRef.current + dt * 1.6);
        const ease = 1 - Math.pow(1 - shockRef.current, 3);
        const rShock = sphereR * (1.04 + ease * 1.75);
        ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, ${((1 - ease) * 0.68).toFixed(2)})`;
        ctx.lineWidth = (2.4 * (1 - ease * 0.55)) * scale;
        ctx.beginPath();
        ctx.arc(cx, cy, rShock, 0, Math.PI * 2);
        ctx.stroke();
      }
    };

    if (mode === 'static') {
      drawFrame(0.016, performance.now());
      const onResizeStatic = () => {
        resize();
        drawFrame(0.016, performance.now());
      };
      window.addEventListener('resize', onResizeStatic);
      return () => window.removeEventListener('resize', onResizeStatic);
    }

    let rafId = 0;
    let lastTime = performance.now();
    let lastFrame = 0;

    const tick = (now: number) => {
      if (pausedRef.current || document.hidden || !document.hasFocus()) {
        rafId = 0;
        return;
      }
      rafId = requestAnimationFrame(tick);
      // Smooth 40fps playing / 15fps paused / 0fps unfocused or occluded by Lyrics
      const minInterval = isPlayingRef.current ? 25 : 66;
      if (now - lastFrame < minInterval) return;
      lastFrame = now;
      const dt = Math.min((now - lastTime) / 1000, 0.08);
      lastTime = now;
      drawFrame(dt, now);
    };

    const startLoop = () => {
      if (rafId !== 0 || pausedRef.current || document.hidden || !document.hasFocus()) return;
      lastTime = performance.now();
      rafId = requestAnimationFrame(tick);
    };

    const stopLoop = () => {
      if (rafId !== 0) {
        cancelAnimationFrame(rafId);
        rafId = 0;
      }
    };

    const syncLoopState = () => {
      if (pausedRef.current || document.hidden || !document.hasFocus()) {
        stopLoop();
      } else {
        startLoop();
      }
    };
    syncLoopRef.current = syncLoopState;

    drawFrame(0.016, performance.now());
    syncLoopState();

    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', onMove, { passive: true });
    window.addEventListener('focus', syncLoopState);
    window.addEventListener('blur', syncLoopState);
    document.addEventListener('visibilitychange', syncLoopState);

    return () => {
      syncLoopRef.current = null;
      stopLoop();
      window.removeEventListener('resize', resize);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('focus', syncLoopState);
      window.removeEventListener('blur', syncLoopState);
      document.removeEventListener('visibilitychange', syncLoopState);
    };
  }, [mode]);

  if (mode === 'oled') {
    return (
      <div className="pointer-events-none fixed inset-0 z-0 bg-black">
        <canvas
          data-bh-webgl="true"
          data-bh-canvas="true"
          width={0}
          height={0}
          className="hidden"
        />
      </div>
    );
  }

  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden bg-[#020308]">
      <canvas
        ref={canvasRef}
        data-bh-webgl="true"
        data-bh-canvas="true"
        style={{
          opacity: 'var(--bh-scroll-opacity, 1)',
          transition: 'opacity 260ms ease',
        }}
        className="absolute inset-0 w-full h-full"
      />
    </div>
  );
});
