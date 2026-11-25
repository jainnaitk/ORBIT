import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Shuffle,
  Repeat,
  Repeat1,
  Heart,
  Download,
  CheckCircle2,
  Loader2,
  AlignLeft,
  ListMusic,
  Minimize2,
  HardDrive,
  Radio,
  Trash2,
  X,
  GripVertical,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import {
  LyricsOverflowMenu,
  LyricsStageBody,
  LyricsSyncModeTabs,
  useLyricsController,
} from './LyricsStageView';
import { bridgeSocket } from '../api/client';
import { useTwoFingerClose } from '../utils/gestures';
import type {
  PlaybackTick,
  QueueData,
  Track,
  DownloadProgress,
} from '../api/types';

export type FullscreenTab = 'lyrics' | 'queue';

interface FloatingGhost {
  track: Track;
  left: number;
  width: number;
  y: number;
}

interface FullscreenStageProps {
  isOpen: boolean;
  initialTab?: FullscreenTab;
  playback: PlaybackTick | null;
  queue: QueueData | null;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  lyricsEdgeBlur: boolean;
  onClose: () => void;
  onTogglePlay: () => void;
  onNext: () => void;
  onPrev: () => void;
  onSeek: (seconds: number) => void;
  onToggleShuffle: () => void;
  onCycleRepeat: () => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onPlayQueueItem: (track: Track, index: number) => void;
  onRemoveQueueItem: (index: number) => void;
  onMoveQueueItem: (fromIdx: number, toIdx: number) => void;
  onClearQueue: () => void;
  onToggleAutoplayRadio: () => void;
  onOpenAlbum?: (track: Track) => void;
  onOpenArtist?: (track: Track) => void;
}

function formatTime(secs?: number): string {
  if (!secs || isNaN(secs) || secs < 0) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

const QUEUE_ROW_HEIGHT = 56;

export const FullscreenStage = ({
  isOpen,
  initialTab = 'lyrics',
  playback,
  queue,
  downloads = {},
  likedIds,
  downloadedIds,
  lyricsEdgeBlur,
  onClose,
  onTogglePlay,
  onNext,
  onPrev,
  onSeek,
  onToggleShuffle,
  onCycleRepeat,
  onToggleLike,
  onDownload,
  onPlayQueueItem,
  onRemoveQueueItem,
  onMoveQueueItem,
  onClearQueue,
  onToggleAutoplayRadio,
  onOpenAlbum,
  onOpenArtist,
}: FullscreenStageProps) => {
  const [rendered, setRendered] = useState<boolean>(isOpen);
  const [visible, setVisible] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<FullscreenTab>(initialTab);
  const stageRef = useRef<HTMLDivElement>(null);

  // Queue drag-and-rearrange state (matching QueueDrawer)
  const [dragVisIdx, setDragVisIdx] = useState<number | null>(null);
  const [overVisIdx, setOverVisIdx] = useState<number | null>(null);
  const [ghost, setGhost] = useState<FloatingGhost | null>(null);
  const dragVisRef = useRef<number | null>(null);
  const overVisRef = useRef<number | null>(null);
  const didDragRef = useRef<boolean>(false);
  const pointerYRef = useRef<number>(0);
  const autoScrollRafRef = useRef<number | null>(null);
  const dragCleanupRef = useRef<(() => void) | null>(null);
  const resetDragTimerRef = useRef<number | null>(null);
  const mountedRef = useRef<boolean>(true);

  useTwoFingerClose({
    enabled: isOpen && rendered,
    ready: visible,
    settleMs: 360,
    containerRef: stageRef,
    onClose,
  });

  // Scrubber state & GPU refs (never store 4Hz time_pos in React state)
  const [isScrubbing, setIsScrubbing] = useState<boolean>(false);
  const [hoverInfo, setHoverInfo] = useState<{ pct: number; sec: number } | null>(null);
  const progressBarRef = useRef<HTMLDivElement>(null);
  const scrubberFillRef = useRef<HTMLDivElement>(null);
  const scrubberKnobRef = useRef<HTMLDivElement>(null);
  const elapsedTextRef = useRef<HTMLSpanElement>(null);
  const lastDisplayedSecRef = useRef<number>(-1);
  const scrubCleanupRef = useRef<(() => void) | null>(null);

  const queueListRef = useRef<HTMLDivElement>(null);
  const lastServerPosRef = useRef<number>(0);
  const lastTickRef = useRef<number>(performance.now());

  const currentTrack = playback?.current_track || queue?.current_track || null;
  const currentVideoId = currentTrack?.video_id ?? null;
  const isBuffering = playback?.state === 'buffering';
  const isActuallyPlaying = playback?.state === 'playing';
  const isPlayingOrBuffering = isActuallyPlaying || isBuffering;
  const duration = playback?.duration || currentTrack?.duration_s || 0;

  const durationRef = useRef<number>(duration);
  durationRef.current = duration;
  const isScrubbingRef = useRef<boolean>(isScrubbing);
  isScrubbingRef.current = isScrubbing;

  useEffect(() => {
    if (isOpen) {
      setActiveTab(initialTab);
      setRendered(true);
      let raf2 = 0;
      const raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => setVisible(true));
      });
      return () => {
        cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);
      };
    } else {
      setVisible(false);
      const t = window.setTimeout(() => setRendered(false), 220);
      return () => clearTimeout(t);
    }
  }, [isOpen, initialTab]);

  // GPU progress bar painter
  const paintProgress = useCallback((posSec: number, durSec: number) => {
    const ratio = durSec > 0 ? Math.min(1, Math.max(0, posSec / durSec)) : 0;
    if (scrubberFillRef.current) {
      scrubberFillRef.current.style.transform = `scaleX(${ratio.toFixed(4)})`;
    }
    if (scrubberKnobRef.current) {
      scrubberKnobRef.current.style.transform = `translate3d(${((ratio - 1) * 100).toFixed(3)}%, 0, 0)`;
    }
    const wholeSec = Math.floor(Math.max(0, posSec));
    if (wholeSec !== lastDisplayedSecRef.current) {
      lastDisplayedSecRef.current = wholeSec;
      if (elapsedTextRef.current) {
        elapsedTextRef.current.textContent = formatTime(wholeSec);
      }
    }
  }, []);

  const syncScrubberFromLyrics = useCallback(
    (targetSec: number) => {
      lastServerPosRef.current = targetSec;
      lastTickRef.current = performance.now();
      paintProgress(targetSec, durationRef.current);
    },
    [paintProgress]
  );

  const lyrics = useLyricsController({
    isActive: isOpen,
    isSyncVisible: isOpen && activeTab === 'lyrics',
    playback,
    queue,
    onSeek,
    onSyncScrubber: syncScrubberFromLyrics,
  });

  // Sync time anchor on open, mount, or track change
  useEffect(() => {
    if (!isOpen || !rendered) return;
    const startPos =
      currentVideoId && bridgeSocket.latestTrackId === currentVideoId
        ? bridgeSocket.latestTimePos
        : playback?.time_pos ?? 0;
    lastServerPosRef.current = startPos;
    lastTickRef.current = performance.now();
    lastDisplayedSecRef.current = -1;
    paintProgress(startPos, durationRef.current);
  }, [isOpen, rendered, currentVideoId, paintProgress]);

  // Direct WebSocket playback_tick listener
  useEffect(() => {
    if (!isOpen) return;
    return bridgeSocket.on('playback_tick', (tick) => {
      if (typeof tick.time_pos === 'number') {
        lastServerPosRef.current = tick.time_pos;
        lastTickRef.current = performance.now();
        if (!isScrubbingRef.current) {
          paintProgress(tick.time_pos, tick.duration || durationRef.current);
        }
      }
    });
  }, [isOpen, paintProgress]);

  // 60fps scrubber interpolation while playing & focused
  useEffect(() => {
    if (!isOpen || !isActuallyPlaying || isScrubbing) return;
    let rafId = 0;

    const frame = (now: number) => {
      if (document.hidden || !document.hasFocus()) {
        rafId = 0;
        return;
      }
      rafId = requestAnimationFrame(frame);
      const elapsed = (now - lastTickRef.current) / 1000;
      const dur = durationRef.current;
      const estimated = Math.min(dur > 0 ? dur : Infinity, lastServerPosRef.current + elapsed);
      paintProgress(estimated, dur);
    };

    const syncLoop = () => {
      if (document.hidden || !document.hasFocus()) {
        if (rafId !== 0) {
          cancelAnimationFrame(rafId);
          rafId = 0;
        }
      } else if (rafId === 0) {
        rafId = requestAnimationFrame(frame);
      }
    };

    syncLoop();
    window.addEventListener('focus', syncLoop);
    window.addEventListener('blur', syncLoop);
    document.addEventListener('visibilitychange', syncLoop);
    return () => {
      if (rafId !== 0) cancelAnimationFrame(rafId);
      window.removeEventListener('focus', syncLoop);
      window.removeEventListener('blur', syncLoop);
      document.removeEventListener('visibilitychange', syncLoop);
    };
  }, [isOpen, isActuallyPlaying, isScrubbing, paintProgress]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      scrubCleanupRef.current?.();
      scrubCleanupRef.current = null;
      dragCleanupRef.current?.();
      dragCleanupRef.current = null;
      if (autoScrollRafRef.current !== null) {
        cancelAnimationFrame(autoScrollRafRef.current);
        autoScrollRafRef.current = null;
      }
      if (resetDragTimerRef.current !== null) {
        clearTimeout(resetDragTimerRef.current);
        resetDragTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!isOpen || activeTab !== 'queue') {
      dragCleanupRef.current?.();
      dragCleanupRef.current = null;
      setDragVisIdx(null);
      setOverVisIdx(null);
      setGhost(null);
    }
  }, [isOpen, activeTab]);

  // Queue entries (excluding currently playing)
  const queueItems = queue?.items || [];
  const currentQueueIndex = queue?.current_index ?? 0;
  const autoplayRadio = queue?.autoplay_radio ?? true;

  const upNextEntries = useMemo(() => {
    if (!queueItems.length) return [];
    return queueItems
      .map((track, origIndex) => ({ track, origIndex }))
      .filter(({ origIndex }) => !(currentTrack && origIndex === currentQueueIndex));
  }, [queueItems, currentQueueIndex, currentTrack]);

  const queueVirtualizer = useVirtualizer({
    count: rendered && activeTab === 'queue' ? upNextEntries.length : 0,
    getScrollElement: () => queueListRef.current,
    estimateSize: () => QUEUE_ROW_HEIGHT,
    overscan: 6,
  });

  const recalcOverIndex = (clientY: number, itemCount: number) => {
    const listEl = queueListRef.current;
    if (!listEl || itemCount <= 0) return;
    const rect = listEl.getBoundingClientRect();
    const contentY = clientY - rect.top + listEl.scrollTop;
    const idx = Math.max(0, Math.min(itemCount - 1, Math.floor(contentY / QUEUE_ROW_HEIGHT)));
    if (idx !== overVisRef.current) {
      overVisRef.current = idx;
      setOverVisIdx(idx);
    }
  };

  const handleQueueRowPointerDown = (e: React.PointerEvent, visIdx: number, track: Track) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('button')) return;

    dragCleanupRef.current?.();

    const listEl = queueListRef.current;
    const listRect = listEl?.getBoundingClientRect();
    const startY = e.clientY;
    const rowLeft = listRect ? listRect.left : 0;
    const rowWidth = listRect ? listRect.width : 360;
    const entriesSnapshot = upNextEntries;
    const totalCount = entriesSnapshot.length;

    dragVisRef.current = visIdx;
    overVisRef.current = visIdx;
    didDragRef.current = false;
    pointerYRef.current = e.clientY;

    const cleanupListeners = () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerUp);
      if (autoScrollRafRef.current !== null) {
        cancelAnimationFrame(autoScrollRafRef.current);
        autoScrollRafRef.current = null;
      }
      if (dragCleanupRef.current === cleanupListeners) {
        dragCleanupRef.current = null;
      }
    };

    const runAutoScroll = () => {
      if (!mountedRef.current || !didDragRef.current || !queueListRef.current) {
        autoScrollRafRef.current = null;
        return;
      }
      const el = queueListRef.current;
      const rect = el.getBoundingClientRect();
      const py = pointerYRef.current;
      const edgeZone = 56;
      let delta = 0;

      if (py < rect.top + edgeZone) {
        const ratio = Math.min(1.6, (rect.top + edgeZone - py) / edgeZone);
        delta = -Math.max(3, ratio * 12);
      } else if (py > rect.bottom - edgeZone) {
        const ratio = Math.min(1.6, (py - (rect.bottom - edgeZone)) / edgeZone);
        delta = Math.max(3, ratio * 12);
      }

      if (delta !== 0) {
        const prevTop = el.scrollTop;
        el.scrollTop += delta;
        if (el.scrollTop !== prevTop) {
          recalcOverIndex(py, totalCount);
        }
      }
      autoScrollRafRef.current = requestAnimationFrame(runAutoScroll);
    };

    const onPointerMove = (moveEvent: PointerEvent) => {
      if (!mountedRef.current) {
        cleanupListeners();
        return;
      }
      pointerYRef.current = moveEvent.clientY;
      if (!didDragRef.current && Math.abs(moveEvent.clientY - startY) < 5) return;
      if (!didDragRef.current) {
        didDragRef.current = true;
        setDragVisIdx(visIdx);
        if (autoScrollRafRef.current === null) {
          autoScrollRafRef.current = requestAnimationFrame(runAutoScroll);
        }
      }

      setGhost({
        track,
        left: rowLeft,
        width: rowWidth,
        y: moveEvent.clientY - QUEUE_ROW_HEIGHT * 0.5,
      });

      recalcOverIndex(moveEvent.clientY, totalCount);
    };

    const onPointerUp = () => {
      cleanupListeners();

      const fromV = dragVisRef.current;
      const toV = overVisRef.current;
      const wasDrag = didDragRef.current;

      dragVisRef.current = null;
      overVisRef.current = null;

      if (!mountedRef.current) return;

      setDragVisIdx(null);
      setOverVisIdx(null);
      setGhost(null);

      if (wasDrag && fromV !== null && toV !== null && fromV !== toV) {
        const fromOrig = entriesSnapshot[fromV]?.origIndex;
        const toOrig = entriesSnapshot[toV]?.origIndex;
        if (fromOrig !== undefined && toOrig !== undefined && fromOrig !== toOrig) {
          onMoveQueueItem(fromOrig, toOrig);
        }
      }
      if (wasDrag) {
        if (resetDragTimerRef.current !== null) clearTimeout(resetDragTimerRef.current);
        resetDragTimerRef.current = window.setTimeout(() => {
          didDragRef.current = false;
          resetDragTimerRef.current = null;
        }, 60);
      }
    };

    dragCleanupRef.current = cleanupListeners;
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);
  };

  const getQueueRowShiftY = (visIdx: number): number => {
    if (dragVisIdx === null || overVisIdx === null || visIdx === dragVisIdx) return 0;
    if (dragVisIdx < overVisIdx && visIdx > dragVisIdx && visIdx <= overVisIdx) return -QUEUE_ROW_HEIGHT;
    if (dragVisIdx > overVisIdx && visIdx >= overVisIdx && visIdx < dragVisIdx) return QUEUE_ROW_HEIGHT;
    return 0;
  };

  // Scrubber pointer handling
  const handleSeekFromPointer = useCallback(
    (e: React.PointerEvent | MouseEvent) => {
      if (!progressBarRef.current || duration <= 0) return;
      const rect = progressBarRef.current.getBoundingClientRect();
      const clickX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
      paintProgress((clickX / rect.width) * duration, duration);
    },
    [duration, paintProgress]
  );

  const onPointerDownScrub = (e: React.PointerEvent) => {
    scrubCleanupRef.current?.();
    setIsScrubbing(true);
    handleSeekFromPointer(e);

    const cleanup = () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerCancel);
      scrubCleanupRef.current = null;
    };
    const onPointerMove = (mv: PointerEvent) => handleSeekFromPointer(mv);
    const onPointerUp = (up: PointerEvent) => {
      if (progressBarRef.current && duration > 0) {
        const rect = progressBarRef.current.getBoundingClientRect();
        const clickX = Math.max(0, Math.min(rect.width, up.clientX - rect.left));
        const finalSec = (clickX / rect.width) * duration;
        onSeek(finalSec);
        lastServerPosRef.current = finalSec;
        lastTickRef.current = performance.now();
        paintProgress(finalSec, duration);
      }
      setIsScrubbing(false);
      cleanup();
    };
    const onPointerCancel = () => {
      setIsScrubbing(false);
      paintProgress(lastServerPosRef.current, duration);
      cleanup();
    };

    scrubCleanupRef.current = cleanup;
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerCancel);
  };

  if (!rendered) return null;

  const isLiked = currentTrack
    ? likedIds
      ? likedIds.has(currentTrack.video_id)
      : !!currentTrack.is_liked
    : false;
  const dlState = currentTrack ? downloads[currentTrack.video_id] : undefined;
  const isDownloading =
    dlState?.status === 'downloading' ||
    dlState?.status === 'queued' ||
    dlState?.status === 'tagging';
  const isDownloaded =
    !!currentTrack &&
    (downloadedIds?.has(currentTrack.video_id) ||
      currentTrack.download_status === 'completed' ||
      dlState?.status === 'completed' ||
      !!currentTrack.local_path);

  return (
    <div
      ref={stageRef}
      role="dialog"
      aria-modal="true"
      aria-label="ORBIT Fullscreen Stage"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'scale3d(1, 1, 1)' : 'scale3d(0.98, 0.98, 1)',
        transition: 'opacity 220ms ease, transform 260ms cubic-bezier(0.22, 1, 0.36, 1)',
        willChange: 'transform, opacity',
        background: `
          radial-gradient(ellipse 68% 58% at 22% 28%, rgba(var(--dynamic-accent-rgb), 0.16) 0%, transparent 62%),
          radial-gradient(ellipse 60% 56% at 82% 76%, rgba(var(--dynamic-secondary-rgb), 0.14) 0%, transparent 64%),
          #05070E
        `,
      }}
      className="fixed inset-0 z-[70] flex flex-col overflow-hidden select-none text-[#F1F4FA]"
    >
      {/* Top Bar */}
      <header
        data-tauri-drag-region
        className="h-12 flex-none px-5 flex items-center justify-between border-b border-white/[0.08] bg-black/25 titlebar-drag"
      >
        <div className="flex items-center gap-2.5 pointer-events-none">
          <span
            className="w-2 h-2 rounded-full"
            style={{
              background: 'var(--dynamic-accent)',
              boxShadow: '0 0 10px var(--dynamic-accent)',
            }}
          />
          <span className="text-[11px] font-bold uppercase tracking-[0.22em] text-[#E6ECF8]">
            Stage Mode
          </span>
        </div>

        <button
          type="button"
          onClick={onClose}
          title="Exit Fullscreen Stage (Esc)"
          className="no-drag flex items-center gap-1.5 px-3 py-1.5 rounded-full ios-liquid-pill text-xs text-[#C8D1EE] hover:text-white"
        >
          <Minimize2 size={13} />
          <span>Exit Stage</span>
        </button>
      </header>

      {/* 50/50 Split Body */}
      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 overflow-hidden">
        {/* LEFT 50%: Large Album Art + Scrubber + Transport */}
        <section className="flex flex-col items-center justify-center px-6 sm:px-10 py-6 md:border-r border-white/[0.08] overflow-y-auto no-scrollbar">
          <div className="w-full max-w-[400px] flex flex-col items-center">
            {/* Album Art with Accent Halo */}
            <div className="relative w-56 h-56 sm:w-72 sm:h-72 lg:w-80 lg:h-80 flex items-center justify-center flex-none">
              <div
                className={`eh-accretion absolute -inset-3 rounded-full opacity-45 pointer-events-none ${
                  isActuallyPlaying ? '' : 'eh-paused opacity-20'
                }`}
                style={{
                  background:
                    'conic-gradient(from 0deg, transparent 0%, rgba(var(--dynamic-accent-rgb), 0.58) 32%, rgba(var(--dynamic-secondary-rgb), 0.30) 64%, transparent 88%)',
                  WebkitMask:
                    'radial-gradient(farthest-side, transparent 89%, #000 91%, #000 96%, transparent 100%)',
                  mask: 'radial-gradient(farthest-side, transparent 89%, #000 91%, #000 96%, transparent 100%)',
                }}
              />
              <button
                type="button"
                onClick={() => {
                  if (currentTrack && onOpenAlbum) {
                    onClose();
                    onOpenAlbum(currentTrack);
                  }
                }}
                title={currentTrack ? `Open Album: ${currentTrack.album || currentTrack.title}` : undefined}
                className="relative w-full h-full rounded-[26px] overflow-hidden border border-white/15 bg-[#0C101C] cursor-pointer hover:scale-[1.015] transition-transform"
                style={{
                  boxShadow:
                    '0 24px 56px rgba(0, 0, 0, 0.78), 0 0 40px rgba(var(--dynamic-accent-rgb), 0.14)',
                }}
              >
                <TrackImage
                  src={currentTrack?.thumbnail_url}
                  videoId={currentTrack?.video_id}
                  size="lg"
                  iconSize={56}
                  eager
                />
              </button>
            </div>

            {/* Track Info & Quick Actions */}
            <div className="mt-6 w-full flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <h1
                  title={currentTrack?.title}
                  className="text-xl sm:text-2xl font-bold text-white leading-tight line-clamp-2"
                >
                  {currentTrack?.title || 'Ready to orbit'}
                </h1>
                {currentTrack && onOpenArtist ? (
                  <button
                    type="button"
                    onClick={() => {
                      onClose();
                      onOpenArtist(currentTrack);
                    }}
                    title={`Open Artist: ${currentTrack.artist}`}
                    className="block max-w-full text-left text-sm text-[#B2BCD9] hover:text-white hover:underline truncate mt-1"
                  >
                    {currentTrack.artist}
                  </button>
                ) : (
                  <p
                    title={currentTrack?.artist}
                    className="text-sm text-[#B2BCD9] truncate mt-1"
                  >
                    {currentTrack?.artist || 'ORBIT Desktop'}
                  </p>
                )}
              </div>

              {currentTrack && (
                <div className="flex items-center gap-1.5 flex-none pt-1">
                  <button
                    type="button"
                    onClick={() => onToggleLike(currentTrack)}
                    title={isLiked ? 'Unlike' : 'Like'}
                    className={`w-9 h-9 rounded-full flex items-center justify-center border transition-colors ${
                      isLiked
                        ? 'bg-[#FF4D8D]/16 text-[#FF4D8D] border-[#FF4D8D]/40'
                        : 'ios-liquid-pill text-[#B2BCD9] hover:text-white'
                    }`}
                  >
                    <Heart size={16} className={isLiked ? 'fill-[#FF4D8D]' : ''} />
                  </button>
                  <button
                    type="button"
                    onClick={() => !isDownloading && !isDownloaded && onDownload(currentTrack)}
                    disabled={isDownloading || isDownloaded}
                    title={
                      isDownloaded
                        ? 'Downloaded offline'
                        : isDownloading
                        ? 'Downloading...'
                        : 'Download track'
                    }
                    className={`w-9 h-9 rounded-full flex items-center justify-center border transition-colors ${
                      isDownloaded
                        ? 'bg-[#34D399]/16 text-[#34D399] border-[#34D399]/40 cursor-default'
                        : 'ios-liquid-pill text-[#B2BCD9] hover:text-white'
                    }`}
                  >
                    {isDownloaded ? (
                      <CheckCircle2 size={16} />
                    ) : isDownloading ? (
                      <Loader2 size={16} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
                    ) : (
                      <Download size={15} />
                    )}
                  </button>
                </div>
              )}
            </div>

            {/* GPU-Composited Scrubber */}
            <div className="w-full mt-5 space-y-1.5">
              <div
                ref={progressBarRef}
                onPointerDown={onPointerDownScrub}
                onMouseMove={(e) => {
                  if (!progressBarRef.current || duration <= 0) return;
                  const rect = progressBarRef.current.getBoundingClientRect();
                  const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
                  setHoverInfo({ pct: (x / rect.width) * 100, sec: (x / rect.width) * duration });
                }}
                onMouseLeave={() => setHoverInfo(null)}
                className="w-full h-5 flex items-center cursor-pointer group relative"
              >
                {hoverInfo && duration > 0 && (
                  <div
                    style={{ left: `${Math.max(6, Math.min(94, hoverInfo.pct))}%` }}
                    className="pointer-events-none absolute -top-7 -translate-x-1/2 px-2 py-0.5 rounded-full ios-liquid-popover text-[10px] font-mono tabular-nums text-[#F4F6FF]"
                  >
                    {formatTime(hoverInfo.sec)}
                  </div>
                )}

                <div className="w-full h-[4.5px] bg-white/[0.15] rounded-full relative group-hover:h-[6px] transition-[height] duration-150">
                  <div className="absolute inset-0 rounded-full overflow-hidden">
                    <div
                      ref={scrubberFillRef}
                      className="w-full h-full origin-left will-change-transform"
                      style={{
                        transform: 'scaleX(0)',
                        background:
                          'linear-gradient(90deg, var(--dynamic-secondary), var(--dynamic-accent), #FFFFFF)',
                        boxShadow: '0 0 12px rgba(var(--dynamic-accent-rgb), 0.55)',
                      }}
                    />
                  </div>
                  <div className="pointer-events-none absolute inset-0">
                    <div
                      ref={scrubberKnobRef}
                      className="w-full h-full relative will-change-transform"
                      style={{ transform: 'translate3d(-100%, 0, 0)' }}
                    >
                      <span
                        className="w-3.5 h-3.5 rounded-full bg-white absolute right-0 top-1/2 translate-x-1/2 -translate-y-1/2"
                        style={{ boxShadow: '0 0 14px var(--dynamic-accent)' }}
                      />
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between text-[11px] font-mono tabular-nums text-[#9AA5C4]">
                <span ref={elapsedTextRef}>0:00</span>
                <span>{formatTime(duration)}</span>
              </div>
            </div>

            {/* Expressive Transport Controls */}
            <div className="mt-4 flex items-center justify-center gap-4">
              <button
                type="button"
                onClick={onToggleShuffle}
                title={queue?.shuffle ? 'Shuffle On' : 'Shuffle Off'}
                className={`w-9 h-9 rounded-full flex items-center justify-center transition-colors ${
                  queue?.shuffle ? 'bg-white/12' : 'text-[#9AA5C4] hover:text-white hover:bg-white/[0.08]'
                }`}
                style={queue?.shuffle ? { color: 'var(--dynamic-accent)' } : undefined}
              >
                <Shuffle size={16} />
              </button>

              <button
                type="button"
                onClick={onPrev}
                title="Previous Track"
                className="w-11 h-11 rounded-full flex items-center justify-center text-[#E6ECF8] hover:text-white hover:bg-white/10 transition-colors"
              >
                <SkipBack size={20} />
              </button>

              <button
                type="button"
                onClick={onTogglePlay}
                title={isBuffering ? 'Buffering...' : isPlayingOrBuffering ? 'Pause' : 'Play'}
                className={`relative w-14 h-14 ios-liquid-orb text-[#020308] flex items-center justify-center hover:scale-105 active:scale-95 eh-play-morph ${
                  isActuallyPlaying ? 'rounded-[16px]' : 'rounded-[28px]'
                }`}
              >
                {isBuffering && (
                  <span
                    className="pointer-events-none absolute -inset-[4px] rounded-full border-2 border-transparent eh-buffer-ring"
                    style={{
                      borderTopColor: 'var(--dynamic-accent)',
                      borderRightColor: 'rgba(var(--dynamic-accent-rgb), 0.45)',
                    }}
                  />
                )}
                {isBuffering ? (
                  <Loader2 size={22} className="eh-buffer-ring" />
                ) : isPlayingOrBuffering ? (
                  <Pause size={22} className="fill-current" />
                ) : (
                  <Play size={22} className="fill-current ml-0.5" />
                )}
              </button>

              <button
                type="button"
                onClick={onNext}
                title="Next Track"
                className="w-11 h-11 rounded-full flex items-center justify-center text-[#E6ECF8] hover:text-white hover:bg-white/10 transition-colors"
              >
                <SkipForward size={20} />
              </button>

              <button
                type="button"
                onClick={onCycleRepeat}
                title={`Repeat: ${queue?.repeat_mode || 'off'}`}
                className={`w-9 h-9 rounded-full flex items-center justify-center transition-colors ${
                  queue?.repeat_mode && queue.repeat_mode !== 'off'
                    ? 'bg-white/12'
                    : 'text-[#9AA5C4] hover:text-white hover:bg-white/[0.08]'
                }`}
                style={
                  queue?.repeat_mode && queue.repeat_mode !== 'off'
                    ? { color: 'var(--dynamic-accent)' }
                    : undefined
                }
              >
                {queue?.repeat_mode === 'one' ? <Repeat1 size={17} /> : <Repeat size={16} />}
              </button>
            </div>
          </div>
        </section>

        {/* RIGHT 50%: Swappable Lyrics / Queue Pane */}
        <section className="flex flex-col min-h-0 overflow-hidden relative">
          {/* Tab Switcher + Contextual Controls */}
          <div className="h-14 flex-none px-5 flex items-center justify-between border-b border-white/[0.08] bg-white/[0.02]">
            <div className="inline-flex items-center p-1 rounded-full bg-white/[0.06] border border-white/[0.10]">
              <button
                type="button"
                onClick={() => setActiveTab('lyrics')}
                className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all ${
                  activeTab === 'lyrics'
                    ? 'bg-white/[0.14] text-white'
                    : 'text-[#9AA5C4] hover:text-white'
                }`}
                style={
                  activeTab === 'lyrics'
                    ? {
                        color: 'var(--dynamic-accent)',
                        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.2)',
                      }
                    : undefined
                }
              >
                <AlignLeft size={13} />
                <span>Lyrics</span>
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('queue')}
                className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all ${
                  activeTab === 'queue'
                    ? 'bg-white/[0.14] text-white'
                    : 'text-[#9AA5C4] hover:text-white'
                }`}
                style={
                  activeTab === 'queue'
                    ? {
                        color: 'var(--dynamic-accent)',
                        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.2)',
                      }
                    : undefined
                }
              >
                <ListMusic size={13} />
                <span>Up Next</span>
                {upNextEntries.length > 0 && (
                  <span className="text-[10px] font-mono tabular-nums opacity-80">
                    {upNextEntries.length}
                  </span>
                )}
              </button>
            </div>

            {activeTab === 'lyrics' ? (
              <div className="flex items-center gap-1.5">
                <LyricsSyncModeTabs controller={lyrics} />
                {lyrics.isOfflineLrc && (
                  <span className="hidden lg:inline-flex items-center gap-1 text-[9px] font-mono uppercase px-2 py-0.5 rounded-full bg-[#34D399]/15 text-[#34D399] border border-[#34D399]/35">
                    <HardDrive size={9} />
                    .lrc
                  </span>
                )}
                {lyrics.syncOffsetMs !== 0 && (
                  <span className="text-[10px] font-mono tabular-nums px-2 py-0.5 rounded-full bg-white/[0.08] text-[#E6ECF8] border border-white/[0.12]">
                    {lyrics.syncOffsetMs > 0
                      ? `+${lyrics.syncOffsetMs}ms`
                      : `${lyrics.syncOffsetMs}ms`}
                  </span>
                )}
                <LyricsOverflowMenu controller={lyrics} />
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={onToggleAutoplayRadio}
                  title="Toggle Autoplay Radio"
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] border transition-colors ${
                    autoplayRadio
                      ? 'bg-white/[0.10] text-white border-[rgba(var(--dynamic-accent-rgb),0.45)]'
                      : 'bg-white/[0.04] text-[#8E99BA] border-white/[0.10] hover:text-white'
                  }`}
                >
                  <Radio
                    size={12}
                    style={autoplayRadio ? { color: 'var(--dynamic-accent)' } : undefined}
                  />
                  <span>Radio</span>
                </button>
                {queueItems.length > 0 && (
                  <button
                    type="button"
                    onClick={onClearQueue}
                    title="Clear Queue"
                    className="p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] hover:bg-white/[0.10] rounded-full transition-colors"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Tab Body */}
          {activeTab === 'lyrics' ? (
            <LyricsStageBody
              variant="fullscreen"
              controller={lyrics}
              lyricsEdgeBlur={lyricsEdgeBlur}
            />
          ) : (
            <div ref={queueListRef} className="flex-1 overflow-y-auto px-4 py-3 relative" style={{ contain: 'strict' }}>
              {upNextEntries.length === 0 ? (
                <div className="h-full flex items-center justify-center text-xs text-[#7A84A6] italic text-center px-6">
                  {currentTrack
                    ? autoplayRadio
                      ? 'Autoplay Radio will queue similar tracks next.'
                      : 'No tracks queued up next.'
                    : 'Queue is empty.'}
                </div>
              ) : (
                <div
                  style={{
                    height: `${queueVirtualizer.getTotalSize()}px`,
                    width: '100%',
                    position: 'relative',
                  }}
                >
                  {queueVirtualizer.getVirtualItems().map((virtualRow) => {
                    const visIdx = virtualRow.index;
                    const entry = upNextEntries[visIdx];
                    if (!entry) return null;
                    const { track: item, origIndex } = entry;
                    const isDragged = dragVisIdx === visIdx;
                    const shiftY = getQueueRowShiftY(visIdx);
                    return (
                      <div
                        key={`${item.video_id}-${origIndex}`}
                        onPointerDown={(e) => handleQueueRowPointerDown(e, visIdx, item)}
                        onClick={() => {
                          if (didDragRef.current) return;
                          onPlayQueueItem(item, origIndex);
                        }}
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          height: `${QUEUE_ROW_HEIGHT}px`,
                          transform: `translate3d(0, ${virtualRow.start + shiftY}px, 0)`,
                          transition:
                            dragVisIdx !== null
                              ? 'transform 180ms cubic-bezier(0.22, 1, 0.36, 1), opacity 140ms ease'
                              : 'background-color 150ms ease',
                          contain: 'layout style paint',
                        }}
                        className={`flex items-center px-3 gap-2.5 rounded-xl hover:bg-white/[0.08] group cursor-grab active:cursor-grabbing ${
                          isDragged ? 'opacity-15' : ''
                        }`}
                      >
                        <div className="w-4 flex-none flex items-center justify-center text-[#646E8F] group-hover:text-[#D4DCFA]">
                          <GripVertical size={13} />
                        </div>
                        <div className="relative w-10 h-10 rounded-lg bg-black border border-white/15 overflow-hidden flex-none">
                          <TrackImage
                            src={item.thumbnail_url}
                            videoId={item.video_id}
                            size="sm"
                            iconSize={15}
                          />
                          <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
                            <Play size={12} className="fill-white text-white ml-0.5" />
                          </div>
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-medium text-[#F4F6FF] truncate">{item.title}</div>
                          <div className="text-[11px] text-[#A6B0CF] truncate">{item.artist}</div>
                        </div>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onRemoveQueueItem(origIndex);
                          }}
                          title="Remove from queue"
                          className="hidden group-hover:flex p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] rounded-full hover:bg-white/[0.08]"
                        >
                          <X size={13} />
                        </button>
                        <span className="text-[11px] font-mono tabular-nums text-[#646E8F] group-hover:hidden">
                          {formatTime(item.duration_s)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      {ghost &&
        createPortal(
          <div
            style={{
              position: 'fixed',
              left: `${ghost.left + 8}px`,
              top: 0,
              width: `${ghost.width - 16}px`,
              height: `${QUEUE_ROW_HEIGHT - 4}px`,
              transform: `translate3d(0, ${ghost.y}px, 0) scale(1.02)`,
              borderColor: 'rgba(var(--dynamic-accent-rgb), 0.70)',
            }}
            className="pointer-events-none z-[9999] ios-liquid-glass rounded-2xl px-3 flex items-center gap-2.5"
          >
            <GripVertical size={13} style={{ color: 'var(--dynamic-accent)' }} className="flex-none" />
            <div className="w-9 h-9 rounded-lg bg-black border border-white/20 overflow-hidden flex-none">
              <TrackImage
                src={ghost.track.thumbnail_url}
                videoId={ghost.track.video_id}
                size="sm"
                iconSize={14}
                eager
              />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-semibold text-white truncate">{ghost.track.title}</div>
              <div className="text-[10px] text-[#A6B0CF] truncate">{ghost.track.artist}</div>
            </div>
          </div>,
          document.body
        )}
    </div>
  );
};
