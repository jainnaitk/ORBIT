import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Heart,
  Maximize2,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import { OrbitLogo } from './OrbitLogo';
import {
  bridgeSocket,
  resizeDynamicIslandWindow,
} from '../api/client';
import type { PlaybackTick, QueueData, Track } from '../api/types';

interface DynamicIslandProps {
  playback: PlaybackTick | null;
  queue: QueueData | null;
  likedIds: Set<string>;
  onTogglePlay: () => void;
  onNext: () => void;
  onPrev: () => void;
  onSeek: (seconds: number) => void;
  onToggleLike: (track: Track) => void;
  onExitIsland: () => void;
}

const AUTO_COLLAPSE_MS = 5000;
const MORPH_MS = 230;

async function startNativeWindowDrag(): Promise<void> {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return;
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window');
    await getCurrentWindow().startDragging();
  } catch {
    // Ignore outside Tauri
  }
}

function formatTime(secs?: number): string {
  if (!secs || isNaN(secs) || secs < 0) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

export const DynamicIsland = ({
  playback,
  queue,
  likedIds,
  onTogglePlay,
  onNext,
  onPrev,
  onSeek,
  onToggleLike,
  onExitIsland,
}: DynamicIslandProps) => {
  const [isCircle, setIsCircle] = useState(false);
  const isCircleRef = useRef<boolean>(false);
  isCircleRef.current = isCircle;
  const collapseTimerRef = useRef<number | null>(null);
  const morphTimerRef = useRef<number | null>(null);
  const hoveringRef = useRef<boolean>(false);
  const switchingRef = useRef<boolean>(false);
  const mountedRef = useRef<boolean>(true);
  const pointerCleanupRef = useRef<(() => void) | null>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const progressFillRef = useRef<HTMLDivElement>(null);
  const elapsedTextRef = useRef<HTMLSpanElement>(null);
  const lastDisplayedSecRef = useRef<number>(-1);

  const currentTrack = playback?.current_track || queue?.current_track || null;
  const isPlaying = playback?.state === 'playing' || playback?.state === 'buffering';
  const duration = playback?.duration || currentTrack?.duration_s || 0;
  const durationRef = useRef<number>(duration);
  durationRef.current = duration;

  const initialPos =
    currentTrack && bridgeSocket.latestTrackId === currentTrack.video_id
      ? bridgeSocket.latestTimePos
      : playback?.time_pos || 0;
  const lastTimePosRef = useRef<number>(initialPos);

  const paintProgress = useCallback((posSec: number, durSec: number, forceText: boolean = false) => {
    lastTimePosRef.current = posSec;
    if (isCircleRef.current) return;
    const ratio = durSec > 0 ? Math.min(1, Math.max(0, posSec / durSec)) : 0;
    if (progressFillRef.current) {
      progressFillRef.current.style.transform = `scaleX(${ratio.toFixed(4)})`;
    }
    const wholeSec = Math.floor(Math.max(0, posSec));
    if (forceText || wholeSec !== lastDisplayedSecRef.current) {
      lastDisplayedSecRef.current = wholeSec;
      if (elapsedTextRef.current) {
        elapsedTextRef.current.textContent = formatTime(wholeSec);
      }
    }
  }, []);

  useEffect(() => {
    const syncedPos =
      currentTrack && bridgeSocket.latestTrackId === currentTrack.video_id
        ? bridgeSocket.latestTimePos
        : playback?.time_pos || 0;
    paintProgress(syncedPos, duration, true);
  }, [playback?.time_pos, currentTrack?.video_id, duration, isCircle, paintProgress]);

  useEffect(() => {
    const unsubTick = bridgeSocket.on('playback_tick', (tick) => {
      if (typeof tick.time_pos === 'number') {
        paintProgress(tick.time_pos, tick.duration || durationRef.current);
      }
    });
    const unsubTrack = bridgeSocket.on('track_changed', (evt) => {
      const nextPos = typeof evt.time_pos === 'number' ? evt.time_pos : 0;
      const nextDur = typeof evt.duration === 'number' ? evt.duration : durationRef.current;
      paintProgress(nextPos, nextDur, true);
    });
    return () => {
      unsubTick();
      unsubTrack();
    };
  }, [paintProgress]);

  const isLiked = currentTrack ? likedIds.has(currentTrack.video_id) : false;

  const clearCollapseTimer = useCallback(() => {
    if (collapseTimerRef.current !== null) {
      clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = null;
    }
  }, []);

  const clearMorphTimer = useCallback(() => {
    if (morphTimerRef.current !== null) {
      clearTimeout(morphTimerRef.current);
      morphTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearCollapseTimer();
      clearMorphTimer();
      pointerCleanupRef.current?.();
      pointerCleanupRef.current = null;
    };
  }, [clearCollapseTimer, clearMorphTimer]);

  // Smooth in-place Pill <-> Circle morph without hiding the window:
  // - Collapsing: CSS-morph down to 58x58 inside 380x142 first, then shrink HWND to 78x78 for click-through.
  // - Expanding: grow HWND to 380x142 first (top-centered circle stays pinned in place), then CSS-morph out to Pill.
  const transitionMode = useCallback(
    async (toCircle: boolean) => {
      if (switchingRef.current || !mountedRef.current) return;
      switchingRef.current = true;
      clearCollapseTimer();
      clearMorphTimer();
      try {
        if (toCircle) {
          setIsCircle(true);
          await new Promise<void>((resolve) => {
            morphTimerRef.current = window.setTimeout(() => {
              morphTimerRef.current = null;
              resolve();
            }, MORPH_MS + 15);
          });
          if (!mountedRef.current) return;
          await resizeDynamicIslandWindow(false);
        } else {
          await resizeDynamicIslandWindow(true);
          if (!mountedRef.current) return;
          await new Promise<void>((resolve) => {
            requestAnimationFrame(() => resolve());
          });
          if (!mountedRef.current) return;
          setIsCircle(false);
        }
      } finally {
        switchingRef.current = false;
      }
    },
    [clearCollapseTimer, clearMorphTimer]
  );

  const scheduleCollapse = useCallback(() => {
    clearCollapseTimer();
    collapseTimerRef.current = window.setTimeout(() => {
      if (mountedRef.current && !hoveringRef.current && !switchingRef.current) {
        void transitionMode(true);
      }
    }, AUTO_COLLAPSE_MS);
  }, [clearCollapseTimer, transitionMode]);

  useEffect(() => {
    if (!isCircle) {
      scheduleCollapse();
    } else {
      clearCollapseTimer();
    }
    return clearCollapseTimer;
  }, [isCircle, scheduleCollapse, clearCollapseTimer]);

  const handleSeekClick = (e: React.MouseEvent) => {
    if (!barRef.current || duration <= 0) return;
    const rect = barRef.current.getBoundingClientRect();
    const clickRatio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const targetSec = clickRatio * duration;
    paintProgress(targetSec, duration, true);
    onSeek(targetSec);
  };

  // Manual drag-vs-tap handler on Circle so a single click ALWAYS expands reliably
  // without Windows WM_NCLBUTTONDOWN swallowing the click event
  const handleCirclePointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    pointerCleanupRef.current?.();
    const startX = e.clientX;
    const startY = e.clientY;
    let startedDrag = false;

    const cleanup = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      if (pointerCleanupRef.current === cleanup) {
        pointerCleanupRef.current = null;
      }
    };

    const onMove = (ev: PointerEvent) => {
      if (!mountedRef.current) {
        cleanup();
        return;
      }
      if (!startedDrag && Math.hypot(ev.clientX - startX, ev.clientY - startY) > 5) {
        startedDrag = true;
        cleanup();
        void startNativeWindowDrag();
      }
    };

    const onUp = () => {
      cleanup();
      if (!startedDrag && mountedRef.current) {
        void transitionMode(false);
      }
    };

    const onCancel = () => {
      cleanup();
    };

    pointerCleanupRef.current = cleanup;
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
  };

  return (
    <div className="w-screen h-screen bg-transparent flex items-start justify-center pt-2 select-none overflow-hidden">
      <div
        data-tauri-drag-region={!isCircle ? '' : undefined}
        onPointerDown={isCircle ? handleCirclePointerDown : undefined}
        onMouseEnter={() => {
          hoveringRef.current = true;
          clearCollapseTimer();
        }}
        onMouseLeave={() => {
          hoveringRef.current = false;
          if (!isCircle) scheduleCollapse();
        }}
        title={
          isCircle
            ? currentTrack
              ? `${currentTrack.title} — Click to expand, drag to move`
              : 'Click to expand ORBIT'
            : undefined
        }
        style={{
          width: isCircle ? 58 : 364,
          height: isCircle ? 58 : 126,
          borderRadius: isCircle ? 9999 : 28,
          transition:
            'width 230ms cubic-bezier(0.22, 1, 0.36, 1), height 230ms cubic-bezier(0.22, 1, 0.36, 1), border-radius 230ms cubic-bezier(0.22, 1, 0.36, 1)',
          willChange: 'width, height, border-radius',
          ...(isCircle
            ? {
                background: isPlaying
                  ? 'conic-gradient(from 0deg, var(--dynamic-accent), rgba(255,255,255,0.52), var(--dynamic-secondary), var(--dynamic-accent))'
                  : 'rgba(255,255,255,0.30)',
              }
            : undefined),
        }}
        className={
          isCircle
            ? 'relative p-[2.5px] flex items-center justify-center cursor-pointer hover:scale-105 active:scale-95 overflow-hidden'
            : 'ios-liquid-island relative overflow-hidden flex flex-col justify-between px-3.5 py-2.5 titlebar-drag'
        }
      >
        {isCircle ? (
          <div className="w-full h-full rounded-full overflow-hidden bg-[#04060D] relative">
            {currentTrack ? (
              <div className={`w-full h-full eh-spin-vinyl ${isPlaying ? '' : 'eh-paused'}`}>
                <TrackImage
                  src={currentTrack.thumbnail_url}
                  videoId={currentTrack.video_id}
                  size="sm"
                  iconSize={20}
                  eager
                />
              </div>
            ) : (
              <OrbitLogo size={52} />
            )}
            <span className="pointer-events-none absolute inset-0 rounded-full ring-1 ring-inset ring-white/30" />
          </div>
        ) : (
          <div className="w-[336px] h-full flex flex-col justify-between">
            <div
              className="pointer-events-none absolute top-0 left-7 right-7 h-[1px]"
              style={{
                background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.55), transparent)',
              }}
            />

            <div className="flex items-center justify-between gap-2.5 relative z-10">
              <div className="flex items-center gap-2.5 min-w-0 flex-1">
                <button
                  type="button"
                  onClick={() => void transitionMode(true)}
                  title="Collapse to Album Circle"
                  className="w-10 h-10 rounded-full p-[1.5px] flex-none no-drag hover:scale-105 transition-transform"
                  style={{
                    background: isPlaying
                      ? 'conic-gradient(from 0deg, var(--dynamic-accent), rgba(255,255,255,0.40), var(--dynamic-secondary), var(--dynamic-accent))'
                      : 'rgba(255,255,255,0.24)',
                  }}
                >
                  <div className="w-full h-full rounded-full overflow-hidden bg-black">
                    {currentTrack ? (
                      <div className={`w-full h-full eh-spin-vinyl ${isPlaying ? '' : 'eh-paused'}`}>
                        <TrackImage
                          src={currentTrack.thumbnail_url}
                          videoId={currentTrack.video_id}
                          size="sm"
                          iconSize={16}
                          eager
                        />
                      </div>
                    ) : (
                      <OrbitLogo size={36} />
                    )}
                  </div>
                </button>

                <div className="min-w-0 flex-1">
                  <div className="text-[12px] font-semibold text-[#F4F6FF] truncate leading-tight">
                    {currentTrack?.title || 'ORBIT'}
                  </div>
                  <div className="text-[10px] text-[#A6B0CF] truncate leading-tight mt-0.5">
                    {currentTrack?.artist || 'Ready to play'}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-1.5 no-drag flex-none">
                {isPlaying && (
                  <div className="flex items-end gap-[2px] h-3 mr-1">
                    <span
                      className="w-[2px] h-full rounded-full eh-eq-bar"
                      style={{ background: 'var(--dynamic-accent)', animationDelay: '0ms' }}
                    />
                    <span
                      className="w-[2px] h-full rounded-full eh-eq-bar"
                      style={{ background: 'var(--dynamic-accent)', animationDelay: '180ms' }}
                    />
                    <span
                      className="w-[2px] h-full rounded-full eh-eq-bar"
                      style={{ background: 'var(--dynamic-accent)', animationDelay: '340ms' }}
                    />
                  </div>
                )}

                <button
                  onClick={onExitIsland}
                  title="Expand to full ORBIT"
                  className="w-7 h-7 rounded-full bg-white/[0.12] hover:bg-white/[0.24] border border-white/20 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
                >
                  <Maximize2 size={11} />
                </button>
              </div>
            </div>

            <div className="flex items-center gap-2 text-[9px] font-mono tabular-nums text-[#A6B0CF] no-drag relative z-10">
              <span ref={elapsedTextRef} className="w-7 text-right">
                {formatTime(lastTimePosRef.current)}
              </span>
              <div
                ref={barRef}
                onClick={handleSeekClick}
                className="flex-1 h-3 flex items-center cursor-pointer group"
              >
                <div className="w-full h-[3.5px] group-hover:h-[5px] bg-white/[0.16] rounded-full overflow-hidden transition-[height] duration-150">
                  <div
                    ref={progressFillRef}
                    className="w-full h-full rounded-full origin-left will-change-transform"
                    style={{
                      transform: `scaleX(${(duration > 0 ? Math.min(1, Math.max(0, lastTimePosRef.current / duration)) : 0).toFixed(4)})`,
                      background:
                        'linear-gradient(90deg, var(--dynamic-secondary), var(--dynamic-accent), #FFFFFF)',
                    }}
                  />
                </div>
              </div>
              <span className="w-7 text-left">{formatTime(duration)}</span>
            </div>

            <div className="flex items-center justify-between px-1 no-drag relative z-10">
              <button
                onClick={() => currentTrack && onToggleLike(currentTrack)}
                disabled={!currentTrack}
                title={isLiked ? 'Unlike' : 'Like'}
                className="w-8 h-8 rounded-full flex items-center justify-center text-[#A6B0CF] hover:text-[#FF4D8D] hover:bg-white/[0.08] disabled:opacity-30 transition-colors"
              >
                <Heart size={14} className={isLiked ? 'fill-[#FF4D8D] text-[#FF4D8D]' : ''} />
              </button>

              <div className="flex items-center gap-3 px-3 py-1 rounded-full bg-white/[0.08] border border-white/15">
                <button
                  onClick={onPrev}
                  title="Previous"
                  className="p-1 text-[#DCE3FC] hover:text-white transition-colors"
                >
                  <SkipBack size={15} />
                </button>
                <button
                  onClick={onTogglePlay}
                  title={isPlaying ? 'Pause' : 'Play'}
                  className="w-8 h-8 rounded-full ios-liquid-orb text-[#020308] flex items-center justify-center hover:scale-105 active:scale-95 transition-transform"
                >
                  {isPlaying ? (
                    <Pause size={14} className="fill-current" />
                  ) : (
                    <Play size={14} className="fill-current ml-0.5" />
                  )}
                </button>
                <button
                  onClick={onNext}
                  title="Next"
                  className="p-1 text-[#DCE3FC] hover:text-white transition-colors"
                >
                  <SkipForward size={15} />
                </button>
              </div>

              <span className="text-[9px] font-bold tracking-[0.22em] text-[#A6B0CF]/80 pr-1">
                ORBIT
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
