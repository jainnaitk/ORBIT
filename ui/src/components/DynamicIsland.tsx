import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Play,
  Pause,
  Loader2,
  SkipBack,
  SkipForward,
  Heart,
  Maximize2,
  Square,
  RectangleHorizontal,
  Circle,
  Pin,
  PinOff,
  X,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import { OrbitLogo } from './OrbitLogo';
import {
  bridgeSocket,
  resizeDynamicIslandWindow,
  setDynamicIslandPinned,
  waitForNextPaint,
  type IslandWindowMode,
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
  onCloseToTray: () => void;
}

const AUTO_COLLAPSE_MS = 5000;

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
  onCloseToTray,
}: DynamicIslandProps) => {
  const [islandMode, setIslandMode] = useState<IslandWindowMode>('pill');
  const [isPinned, setIsPinned] = useState<boolean>(true);
  const islandModeRef = useRef<IslandWindowMode>('pill');
  islandModeRef.current = islandMode;
  const isPinnedRef = useRef<boolean>(true);
  isPinnedRef.current = isPinned;
  const lastExpandedModeRef = useRef<'pill' | 'card'>('pill');
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

  const isCircle = islandMode === 'circle';
  const isCard = islandMode === 'card';

  const currentTrack = playback?.current_track || queue?.current_track || null;
  const isBuffering = playback?.state === 'buffering';
  const isActuallyPlaying = playback?.state === 'playing';
  const isPlaying = isActuallyPlaying || isBuffering;
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
    if (islandModeRef.current === 'circle') return;
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
  }, [playback?.time_pos, currentTrack?.video_id, duration, islandMode, paintProgress]);

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

  // Zero-clip GPU morph between Pill, Circle, and Square Art-Card (220x220):
  // - Collapsing/shrinking: smooth scale/fade out (130ms) -> render target -> wait 2 rAFs -> resize HWND.
  // - Expanding: grow HWND first -> wait until viewport commits required dimensions -> render target cleanly.
  const [morphPhase, setMorphPhase] = useState<'idle' | 'collapsing'>('idle');

  const waitForMinViewport = useCallback((minW: number, minH: number, timeoutMs: number = 120) => {
    return new Promise<void>((resolve) => {
      const deadline = performance.now() + timeoutMs;
      const check = () => {
        if (
          !mountedRef.current ||
          (window.innerWidth >= minW && window.innerHeight >= minH) ||
          performance.now() >= deadline
        ) {
          requestAnimationFrame(() => resolve());
          return;
        }
        requestAnimationFrame(check);
      };
      requestAnimationFrame(check);
    });
  }, []);

  const transitionMode = useCallback(
    async (target: IslandWindowMode) => {
      if (switchingRef.current || !mountedRef.current || islandModeRef.current === target) return;
      switchingRef.current = true;
      clearCollapseTimer();
      clearMorphTimer();
      try {
        if (target === 'pill' || target === 'card') {
          lastExpandedModeRef.current = target;
        }

        if (target === 'circle') {
          setMorphPhase('collapsing');
          await new Promise<void>((resolve) => {
            morphTimerRef.current = window.setTimeout(() => {
              morphTimerRef.current = null;
              resolve();
            }, 135);
          });
          if (!mountedRef.current) return;
          setIslandMode('circle');
          setMorphPhase('idle');
          await waitForNextPaint();
          if (!mountedRef.current) return;
          await resizeDynamicIslandWindow('circle');
        } else if (target === 'card') {
          setMorphPhase('collapsing');
          await new Promise<void>((resolve) => {
            morphTimerRef.current = window.setTimeout(() => {
              morphTimerRef.current = null;
              resolve();
            }, 110);
          });
          if (!mountedRef.current) return;
          await resizeDynamicIslandWindow('card');
          if (!mountedRef.current) return;
          await waitForMinViewport(220, 220, 130);
          if (!mountedRef.current) return;
          setIslandMode('card');
          setMorphPhase('idle');
        } else {
          // Target: 'pill'
          if (islandModeRef.current === 'card') {
            setMorphPhase('collapsing');
            await new Promise<void>((resolve) => {
              morphTimerRef.current = window.setTimeout(() => {
                morphTimerRef.current = null;
                resolve();
              }, 110);
            });
            if (!mountedRef.current) return;
          }
          await resizeDynamicIslandWindow('pill');
          if (!mountedRef.current) return;
          await waitForMinViewport(350, 126, 130);
          if (!mountedRef.current) return;
          setIslandMode('pill');
          setMorphPhase('idle');
        }
      } finally {
        switchingRef.current = false;
      }
    },
    [clearCollapseTimer, clearMorphTimer, waitForMinViewport]
  );

  const scheduleCollapse = useCallback(() => {
    clearCollapseTimer();
    collapseTimerRef.current = window.setTimeout(() => {
      if (
        mountedRef.current &&
        islandModeRef.current === 'pill' &&
        !hoveringRef.current &&
        !switchingRef.current
      ) {
        void transitionMode('circle');
      }
    }, AUTO_COLLAPSE_MS);
  }, [clearCollapseTimer, transitionMode]);

  useEffect(() => {
    if (islandMode === 'pill') {
      scheduleCollapse();
    } else {
      clearCollapseTimer();
    }
    return clearCollapseTimer;
  }, [islandMode, scheduleCollapse, clearCollapseTimer]);

  const handleTogglePin = useCallback(() => {
    setIsPinned((prev) => {
      const next = !prev;
      isPinnedRef.current = next;
      void setDynamicIslandPinned(next);
      return next;
    });
  }, []);

  useEffect(() => {
    void setDynamicIslandPinned(isPinnedRef.current);
  }, []);

  const handleCloseToTray = useCallback(() => {
    clearCollapseTimer();
    clearMorphTimer();
    pointerCleanupRef.current?.();
    pointerCleanupRef.current = null;
    switchingRef.current = true;
    onCloseToTray();
  }, [clearCollapseTimer, clearMorphTimer, onCloseToTray]);

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
        void transitionMode(lastExpandedModeRef.current);
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

  const cardWidth = isCircle ? 58 : isCard ? 220 : 388;
  const cardHeight = isCircle ? 58 : isCard ? 220 : 126;
  const cardRadius = isCircle ? 9999 : isCard ? 26 : 28;

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
          if (islandMode === 'pill') scheduleCollapse();
        }}
        title={
          isCircle
            ? currentTrack
              ? `${currentTrack.title} — Click to expand, drag anywhere to move${isPinned ? ' (Pinned on top)' : ''}`
              : 'Click to expand ORBIT'
            : undefined
        }
        style={{
          width: cardWidth,
          height: cardHeight,
          borderRadius: cardRadius,
          transformOrigin: 'top center',
          transform: morphPhase === 'collapsing' ? 'scale3d(0.72, 0.72, 1)' : undefined,
          opacity: morphPhase === 'collapsing' ? 0 : 1,
          transition:
            'transform 130ms cubic-bezier(0.22, 1, 0.36, 1), opacity 120ms ease',
          willChange: 'transform, opacity',
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
            ? `relative p-[2.5px] flex items-center justify-center cursor-pointer hover:scale-105 active:scale-95 overflow-hidden ${
                morphPhase === 'idle' ? 'eh-island-circle-in' : ''
              }`
            : isCard
            ? `ios-liquid-island relative overflow-hidden flex flex-col justify-between titlebar-drag ${
                morphPhase === 'idle' ? 'eh-island-in' : ''
              }`
            : `ios-liquid-island relative overflow-hidden flex flex-col justify-between px-3.5 py-2.5 titlebar-drag ${
                morphPhase === 'idle' ? 'eh-island-in' : ''
              }`
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
        ) : isCard ? (
          /* Square Art-Card Mode (220x220) with full-bleed art, dark scrim & centered controls */
          <div
            data-tauri-drag-region
            className="relative w-full h-full flex flex-col justify-between p-3 titlebar-drag"
          >
            {/* Full-bleed Album Art */}
            <div className="pointer-events-none absolute inset-0 bg-[#05070E]">
              {currentTrack ? (
                <TrackImage
                  src={currentTrack.thumbnail_url}
                  videoId={currentTrack.video_id}
                  size="lg"
                  iconSize={48}
                  eager
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center">
                  <OrbitLogo size={76} />
                </div>
              )}
            </div>

            {/* Dark Obsidian Scrim for Contrast */}
            <div
              className="pointer-events-none absolute inset-0"
              style={{
                background:
                  'linear-gradient(180deg, rgba(5,7,14,0.68) 0%, rgba(5,7,14,0.36) 44%, rgba(5,7,14,0.92) 100%)',
              }}
            />

            {/* Top Action Bar */}
            <div className="relative z-10 flex items-center justify-between no-drag">
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void transitionMode('circle')}
                  title="Collapse to Album Circle"
                  className="w-7 h-7 rounded-full bg-black/45 hover:bg-black/70 border border-white/20 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
                >
                  <Circle size={11} />
                </button>
                <button
                  type="button"
                  onClick={handleTogglePin}
                  title={isPinned ? 'Pinned on top — drag anywhere to move (click to unpin)' : 'Pin on top across apps'}
                  className={`w-7 h-7 rounded-full border flex items-center justify-center transition-colors ${
                    isPinned
                      ? 'bg-black/65 border-[rgba(var(--dynamic-accent-rgb),0.65)]'
                      : 'bg-black/45 hover:bg-black/70 border-white/20 text-[#DCE3FC] hover:text-white'
                  }`}
                  style={isPinned ? { color: 'var(--dynamic-accent)' } : undefined}
                >
                  {isPinned ? <Pin size={11} /> : <PinOff size={11} />}
                </button>
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void transitionMode('pill')}
                  title="Switch to Compact Pill"
                  className="w-7 h-7 rounded-full bg-black/45 hover:bg-black/70 border border-white/20 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
                >
                  <RectangleHorizontal size={12} />
                </button>
                <button
                  type="button"
                  onClick={onExitIsland}
                  title="Expand to full ORBIT"
                  className="w-7 h-7 rounded-full bg-black/45 hover:bg-black/70 border border-white/20 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
                >
                  <Maximize2 size={11} />
                </button>
                <button
                  type="button"
                  onClick={handleCloseToTray}
                  title="Close to System Tray"
                  className="w-7 h-7 rounded-full bg-black/45 hover:bg-[#E81123] border border-white/20 hover:border-[#FF4D8D]/60 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
                >
                  <X size={12} />
                </button>
              </div>
            </div>

            {/* Centered Transport Controls */}
            <div className="relative z-10 flex items-center justify-center gap-3 no-drag my-auto">
              <button
                type="button"
                onClick={onPrev}
                title="Previous"
                className="w-9 h-9 rounded-full bg-black/45 hover:bg-black/70 border border-white/15 text-white flex items-center justify-center transition-colors"
              >
                <SkipBack size={16} />
              </button>

              <button
                type="button"
                onClick={onTogglePlay}
                title={isBuffering ? 'Buffering...' : isPlaying ? 'Pause' : 'Play'}
                className={`relative w-12 h-12 ios-liquid-orb text-[#020308] flex items-center justify-center hover:scale-105 active:scale-95 eh-play-morph ${
                  isActuallyPlaying ? 'rounded-[14px]' : 'rounded-[24px]'
                }`}
              >
                {isBuffering && (
                  <span
                    className="pointer-events-none absolute -inset-[3px] rounded-full border-2 border-transparent eh-buffer-ring"
                    style={{
                      borderTopColor: 'var(--dynamic-accent)',
                      borderRightColor: 'rgba(var(--dynamic-accent-rgb), 0.45)',
                    }}
                  />
                )}
                {isBuffering ? (
                  <Loader2 size={18} className="eh-buffer-ring" />
                ) : isPlaying ? (
                  <Pause size={18} className="fill-current" />
                ) : (
                  <Play size={18} className="fill-current ml-0.5" />
                )}
              </button>

              <button
                type="button"
                onClick={onNext}
                title="Next"
                className="w-9 h-9 rounded-full bg-black/45 hover:bg-black/70 border border-white/15 text-white flex items-center justify-center transition-colors"
              >
                <SkipForward size={16} />
              </button>
            </div>

            {/* Bottom Metadata + Mini Scrubber */}
            <div className="relative z-10 space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-bold text-white truncate leading-tight">
                    {currentTrack?.title || 'ORBIT'}
                  </div>
                  <div className="text-[10px] text-[#C8D1EE] truncate leading-tight mt-0.5">
                    {currentTrack?.artist || 'Ready to play'}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => currentTrack && onToggleLike(currentTrack)}
                  disabled={!currentTrack}
                  title={isLiked ? 'Unlike' : 'Like'}
                  className="w-7 h-7 rounded-full flex items-center justify-center text-[#DCE3FC] hover:text-[#FF4D8D] hover:bg-white/10 disabled:opacity-30 transition-colors no-drag flex-none"
                >
                  <Heart size={13} className={isLiked ? 'fill-[#FF4D8D] text-[#FF4D8D]' : ''} />
                </button>
              </div>

              <div
                ref={barRef}
                onClick={handleSeekClick}
                className="w-full h-2.5 flex items-center cursor-pointer group no-drag"
              >
                <div className="w-full h-[3px] group-hover:h-[4.5px] bg-white/[0.22] rounded-full overflow-hidden transition-[height] duration-150">
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
            </div>
          </div>
        ) : (
          <div className="w-[360px] h-full flex flex-col justify-between">
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
                  onClick={() => void transitionMode('circle')}
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
                  <div className="flex items-end gap-[2px] h-3 mr-0.5">
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
                  type="button"
                  onClick={handleTogglePin}
                  title={isPinned ? 'Pinned on top — drag anywhere to move (click to unpin)' : 'Pin on top across apps'}
                  className={`w-7 h-7 rounded-full border flex items-center justify-center transition-colors ${
                    isPinned
                      ? 'bg-white/[0.18] border-[rgba(var(--dynamic-accent-rgb),0.65)]'
                      : 'bg-white/[0.12] hover:bg-white/[0.24] border-white/20 text-[#DCE3FC] hover:text-white'
                  }`}
                  style={isPinned ? { color: 'var(--dynamic-accent)' } : undefined}
                >
                  {isPinned ? <Pin size={11} /> : <PinOff size={11} />}
                </button>

                <button
                  type="button"
                  onClick={() => void transitionMode('card')}
                  title="Switch to Square Art-Card (220x220)"
                  className="w-7 h-7 rounded-full bg-white/[0.12] hover:bg-white/[0.24] border border-white/20 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
                >
                  <Square size={10} />
                </button>

                <button
                  type="button"
                  onClick={onExitIsland}
                  title="Expand to full ORBIT"
                  className="w-7 h-7 rounded-full bg-white/[0.12] hover:bg-white/[0.24] border border-white/20 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
                >
                  <Maximize2 size={11} />
                </button>

                <button
                  type="button"
                  onClick={handleCloseToTray}
                  title="Close to System Tray"
                  className="w-7 h-7 rounded-full bg-white/[0.12] hover:bg-[#E81123] border border-white/20 hover:border-[#FF4D8D]/60 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
                >
                  <X size={12} />
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
                  title={isBuffering ? 'Buffering...' : isPlaying ? 'Pause' : 'Play'}
                  className={`relative w-8 h-8 ios-liquid-orb text-[#020308] flex items-center justify-center hover:scale-105 active:scale-95 eh-play-morph ${
                    isActuallyPlaying ? 'rounded-[10px]' : 'rounded-[16px]'
                  }`}
                >
                  {isBuffering && (
                    <span
                      className="pointer-events-none absolute -inset-[3px] rounded-full border-2 border-transparent eh-buffer-ring"
                      style={{
                        borderTopColor: 'var(--dynamic-accent)',
                        borderRightColor: 'rgba(var(--dynamic-accent-rgb), 0.45)',
                      }}
                    />
                  )}
                  {isBuffering ? (
                    <Loader2 size={14} className="eh-buffer-ring" />
                  ) : isPlaying ? (
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
