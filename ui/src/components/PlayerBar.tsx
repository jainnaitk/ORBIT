import { useEffect, useRef, useState, useCallback, type CSSProperties } from 'react';
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
  ListMusic,
  Volume2,
  VolumeX,
  AlignLeft,
  SlidersHorizontal,
  Maximize2,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import { bridgeSocket } from '../api/client';
import type { PlaybackTick, QueueData, Track, DownloadProgress, EqPreset } from '../api/types';

function MarqueeText({
  text,
  className = '',
  onClick,
}: {
  text: string;
  className?: string;
  onClick?: () => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const spanRef = useRef<HTMLSpanElement>(null);
  const [overflowPx, setOverflowPx] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    const measure = () => {
      if (cancelled) return;
      const wrap = wrapRef.current;
      const span = spanRef.current;
      if (!wrap || !span) return;
      const diff = Math.ceil(span.scrollWidth - wrap.clientWidth);
      setOverflowPx(diff > 2 ? diff : 0);
    };
    measure();
    if (typeof document !== 'undefined' && document.fonts?.ready) {
      void document.fonts.ready.then(measure);
    }
    if (typeof ResizeObserver === 'undefined' || !wrapRef.current) {
      return () => {
        cancelled = true;
      };
    }
    const ro = new ResizeObserver(measure);
    ro.observe(wrapRef.current);
    return () => {
      cancelled = true;
      ro.disconnect();
    };
  }, [text]);

  const marqueeStyle =
    overflowPx > 0
      ? ({
          '--marquee-shift': `-${overflowPx + 10}px`,
          '--marquee-duration': `${Math.max(7, Math.min(18, overflowPx / 16 + 5)).toFixed(1)}s`,
        } as CSSProperties)
      : undefined;

  return (
    <div
      ref={wrapRef}
      onClick={onClick}
      title={text}
      className={`eh-marquee-wrap min-w-0 flex-1 ${className}`}
    >
      <span
        ref={spanRef}
        style={marqueeStyle}
        className={`eh-marquee-track ${overflowPx > 0 ? 'eh-marquee-active' : ''}`}
      >
        {text}
      </span>
    </div>
  );
}

const EQ_OPTIONS: { id: EqPreset; label: string; desc: string }[] = [
  { id: 'flat', label: 'Flat', desc: 'Original studio mix' },
  { id: 'bass', label: 'Deep Bass', desc: 'Rich sub-bass punch' },
  { id: 'vocal', label: 'Vocal Boost', desc: 'Crisp lyrics & mids' },
  { id: 'night', label: 'Late Night', desc: 'Balanced low-volume dynamics' },
  { id: 'studio', label: 'Studio', desc: 'Subtle V-curve clarity' },
];

interface PlayerBarProps {
  playback: PlaybackTick | null;
  queue: QueueData | null;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  eqPreset: EqPreset;
  isQueueOpen: boolean;
  isLyricsOpen: boolean;
  isFullscreenOpen?: boolean;
  transitionKey: number;
  onChangeEqPreset: (preset: EqPreset) => void;
  onToggleQueue: () => void;
  onToggleLyrics: () => void;
  onToggleFullscreen: () => void;
  onOpenFullLyrics: () => void;
  onOpenAlbum: (track: Track) => void;
  onOpenArtist: (track: Track) => void;
  onTogglePlay: () => void;
  onNext: () => void;
  onPrev: () => void;
  onSeek: (seconds: number) => void;
  onSetVolume: (volume: number) => void;
  onToggleShuffle: () => void;
  onCycleRepeat: () => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
}

function formatTime(secs?: number): string {
  if (!secs || isNaN(secs) || secs < 0) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

export const PlayerBar = ({
  playback,
  queue,
  downloads = {},
  likedIds,
  downloadedIds,
  eqPreset,
  isQueueOpen,
  isLyricsOpen,
  isFullscreenOpen = false,
  transitionKey,
  onChangeEqPreset,
  onToggleQueue,
  onToggleLyrics,
  onToggleFullscreen,
  onOpenFullLyrics,
  onOpenAlbum,
  onOpenArtist,
  onTogglePlay,
  onNext,
  onPrev,
  onSeek,
  onSetVolume,
  onToggleShuffle,
  onCycleRepeat,
  onToggleLike,
  onDownload,
}: PlayerBarProps) => {
  const currentTrack = playback?.current_track || queue?.current_track || null;
  const currentVideoId = currentTrack?.video_id ?? null;

  const isPlayingOrBuffering =
    playback?.state === 'playing' || playback?.state === 'buffering';
  const isActuallyPlaying = playback?.state === 'playing';
  const duration = playback?.duration || currentTrack?.duration_s || 0;

  const [isScrubbing, setIsScrubbing] = useState<boolean>(false);
  const [hoverInfo, setHoverInfo] = useState<{ pct: number; sec: number } | null>(null);
  const [isEqOpen, setIsEqOpen] = useState<boolean>(false);

  const lastServerPosRef = useRef<number>(0);
  const lastTickTimeRef = useRef<number>(performance.now());
  const durationRef = useRef<number>(0);
  const lastDisplayedSecRef = useRef<number>(-1);
  const scrubCleanupRef = useRef<(() => void) | null>(null);

  const progressBarRef = useRef<HTMLDivElement>(null);
  const mobileProgressFillRef = useRef<HTMLDivElement>(null);
  const scrubberFillRef = useRef<HTMLDivElement>(null);
  const scrubberKnobTrackRef = useRef<HTMLDivElement>(null);
  const elapsedTextRef = useRef<HTMLSpanElement>(null);

  durationRef.current = duration;

  // Pure GPU-composited scaleX + translate3d updater: zero layout/reflow or backdrop re-blur thrash
  const paintProgress = useCallback((posSec: number, durSec: number) => {
    const ratio = durSec > 0 ? Math.min(1, Math.max(0, posSec / durSec)) : 0;
    const scaleCss = `scaleX(${ratio.toFixed(4)})`;
    if (mobileProgressFillRef.current) mobileProgressFillRef.current.style.transform = scaleCss;
    if (scrubberFillRef.current) scrubberFillRef.current.style.transform = scaleCss;
    if (scrubberKnobTrackRef.current) {
      scrubberKnobTrackRef.current.style.transform = `translate3d(${((ratio - 1) * 100).toFixed(3)}%, 0, 0)`;
    }
    const wholeSec = Math.floor(Math.max(0, posSec));
    if (wholeSec !== lastDisplayedSecRef.current) {
      lastDisplayedSecRef.current = wholeSec;
      if (elapsedTextRef.current) {
        elapsedTextRef.current.textContent = formatTime(wholeSec);
      }
    }
  }, []);

  useEffect(() => {
    const startPos =
      currentVideoId && bridgeSocket.latestTrackId === currentVideoId
        ? bridgeSocket.latestTimePos
        : 0;
    lastServerPosRef.current = startPos;
    lastTickTimeRef.current = performance.now();
    lastDisplayedSecRef.current = -1;
    paintProgress(startPos, durationRef.current);
  }, [currentVideoId, paintProgress]);

  const isScrubbingRef = useRef<boolean>(false);
  isScrubbingRef.current = isScrubbing;

  useEffect(() => {
    if (playback?.time_pos !== undefined) {
      const syncedPos =
        currentVideoId && bridgeSocket.latestTrackId === currentVideoId
          ? bridgeSocket.latestTimePos
          : playback.time_pos;
      lastServerPosRef.current = syncedPos;
      lastTickTimeRef.current = performance.now();
      if (!isScrubbing) {
        paintProgress(syncedPos, duration);
      }
    }
  }, [playback?.time_pos, currentVideoId, duration, isScrubbing, paintProgress]);

  // Direct WebSocket time_pos sync so 250ms ticks never force full-app React re-renders
  useEffect(() => {
    return bridgeSocket.on('playback_tick', (tick) => {
      if (typeof tick.time_pos === 'number') {
        lastServerPosRef.current = tick.time_pos;
        lastTickTimeRef.current = performance.now();
        if (!isScrubbingRef.current) {
          paintProgress(tick.time_pos, tick.duration || durationRef.current);
        }
      }
    });
  }, [paintProgress]);

  // Native 60fps GPU-composited interpolation (zero React re-renders, pauses when unfocused/hidden)
  useEffect(() => {
    if (!isActuallyPlaying || isScrubbing) return;
    let rafId = 0;

    const frame = (now: number) => {
      if (document.hidden || !document.hasFocus()) {
        rafId = 0;
        return;
      }
      rafId = requestAnimationFrame(frame);
      const elapsed = (now - lastTickTimeRef.current) / 1000;
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
  }, [isActuallyPlaying, isScrubbing, paintProgress]);

  useEffect(() => {
    return () => {
      scrubCleanupRef.current?.();
      scrubCleanupRef.current = null;
    };
  }, []);

  const handleSeekFromPointer = useCallback(
    (e: React.PointerEvent | MouseEvent) => {
      if (!progressBarRef.current || duration <= 0) return;
      const rect = progressBarRef.current.getBoundingClientRect();
      const clickX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
      const targetSec = (clickX / rect.width) * duration;
      paintProgress(targetSec, duration);
    },
    [duration, paintProgress]
  );

  const onPointerDown = (e: React.PointerEvent) => {
    scrubCleanupRef.current?.();
    setIsScrubbing(true);
    handleSeekFromPointer(e);

    const cleanup = () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerCancel);
      scrubCleanupRef.current = null;
    };
    const onPointerMove = (moveEvent: PointerEvent) => handleSeekFromPointer(moveEvent);
    const onPointerUp = (upEvent: PointerEvent) => {
      if (progressBarRef.current && duration > 0) {
        const rect = progressBarRef.current.getBoundingClientRect();
        const clickX = Math.max(0, Math.min(rect.width, upEvent.clientX - rect.left));
        const finalSec = (clickX / rect.width) * duration;
        onSeek(finalSec);
        lastServerPosRef.current = finalSec;
        lastTickTimeRef.current = performance.now();
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

  const handleBarHover = (e: React.MouseEvent) => {
    if (!progressBarRef.current || duration <= 0) return;
    const rect = progressBarRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
    setHoverInfo({ pct: (x / rect.width) * 100, sec: (x / rect.width) * duration });
  };

  const [volume, setVolume] = useState<number>(playback?.volume ?? 80);
  const [prevVolume, setPrevVolume] = useState<number>(80);

  useEffect(() => {
    if (playback?.volume !== undefined) setVolume(playback.volume);
  }, [playback?.volume]);

  const handleVolumeChange = (newVol: number) => {
    setVolume(newVol);
    onSetVolume(newVol);
  };

  const toggleMute = () => {
    if (volume > 0) {
      setPrevVolume(volume);
      handleVolumeChange(0);
    } else {
      handleVolumeChange(prevVolume || 80);
    }
  };

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

  const queueCount = queue?.items?.length || 0;

  return (
    <footer className="w-full max-w-[1120px] mx-auto h-[68px] sm:h-[80px] flex-none ios-liquid-glass rounded-[22px] sm:rounded-[26px] px-2.5 sm:px-3.5 lg:px-4 flex items-center justify-between gap-2 lg:gap-3 z-50 select-none relative overflow-visible">
      {/* Compact mobile-only progress bar (hidden on >=sm where the center scrubber is shown) */}
      <div className="sm:hidden pointer-events-none absolute top-0 left-5 right-5 h-[2px] overflow-hidden rounded-full opacity-90">
        <div
          ref={mobileProgressFillRef}
          className="w-full h-full origin-left will-change-transform"
          style={{
            transform: 'scaleX(0)',
            background:
              'linear-gradient(90deg, transparent, rgba(var(--dynamic-secondary-rgb),0.75), var(--dynamic-accent))',
          }}
        />
      </div>

      {/* Left: Spinning Album Art + Rising Track Meta + Heart */}
      <div className="flex-1 sm:flex-none sm:w-[175px] md:w-[225px] lg:w-[270px] min-w-0 h-full flex items-center overflow-hidden">
        <div
          key={`${currentVideoId || 'empty'}-${transitionKey}`}
          className={`flex items-center gap-2.5 sm:gap-3 w-full min-w-0 ${
            transitionKey > 0 ? 'eh-rise-card' : ''
          }`}
        >
          <button
            type="button"
            onClick={() => currentTrack && onOpenAlbum(currentTrack)}
            title={currentTrack ? `Open Album: ${currentTrack.album || currentTrack.title}` : 'Ready'}
            className="group relative w-10 h-10 sm:w-[50px] sm:h-[50px] rounded-full p-[2px] flex-none transition-transform hover:scale-105 cursor-pointer"
            style={{
              background: isPlayingOrBuffering
                ? 'conic-gradient(from 0deg, rgba(var(--dynamic-accent-rgb), 0.9), rgba(255,255,255,0.28), rgba(var(--dynamic-secondary-rgb), 0.75), rgba(var(--dynamic-accent-rgb), 0.9))'
                : 'rgba(255,255,255,0.16)',
            }}
          >
            <div className="w-full h-full rounded-full overflow-hidden bg-black relative">
              <div className={`w-full h-full eh-spin-vinyl ${isPlayingOrBuffering ? '' : 'eh-paused'}`}>
                <TrackImage
                  src={currentTrack?.thumbnail_url}
                  videoId={currentTrack?.video_id}
                  size="sm"
                  iconSize={16}
                  eager
                />
              </div>
              <span className="pointer-events-none absolute inset-0 rounded-full ring-1 ring-inset ring-white/25" />
            </div>
          </button>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 min-w-0">
              <MarqueeText
                text={currentTrack?.title || 'Ready to orbit'}
                onClick={() => currentTrack && onOpenFullLyrics()}
                className="text-xs sm:text-[13px] font-semibold text-[#F4F6FF] hover:underline cursor-pointer"
              />
              {isActuallyPlaying && (
                <div className="hidden sm:flex items-end gap-[2px] h-2.5 flex-none">
                  <span
                    className="w-[2px] h-full rounded-full eh-eq-bar"
                    style={{ background: 'var(--dynamic-accent)' }}
                  />
                  <span
                    className="w-[2px] h-full rounded-full eh-eq-bar"
                    style={{ background: 'var(--dynamic-accent)', animationDelay: '180ms' }}
                  />
                </div>
              )}
            </div>
            <div className="text-[10px] sm:text-[11px] text-[#9BA4C4] flex items-center gap-1 mt-0.5 min-w-0">
              {isDownloaded && (
                <span
                  title="Downloaded offline"
                  className="inline-flex items-center text-[#34D399] flex-none"
                >
                  <CheckCircle2 size={11} />
                </span>
              )}
              <MarqueeText
                text={currentTrack?.artist || 'ORBIT Desktop'}
                onClick={() => currentTrack && onOpenArtist(currentTrack)}
                className={currentTrack ? 'hover:text-white hover:underline cursor-pointer' : ''}
              />
            </div>
          </div>

          {currentTrack && (
            <button
              onClick={() => onToggleLike(currentTrack)}
              title={isLiked ? 'Unlike' : 'Like'}
              className="hidden md:flex w-8 h-8 rounded-full items-center justify-center text-[#9BA4C4] hover:text-[#FF4D8D] hover:bg-white/[0.08] transition-colors flex-none"
            >
              <Heart size={15} className={isLiked ? 'fill-[#FF4D8D] text-[#FF4D8D]' : ''} />
            </button>
          )}
        </div>
      </div>

      {/* Center: Sculpted Liquid Glass Transport Pill + Precision Orbital Scrubber */}
      <div className="flex flex-col items-center gap-1.5 flex-none sm:flex-1 sm:max-w-[440px]">
        <div
          className="flex items-center gap-1.5 sm:gap-2.5 px-2 sm:px-3 py-1 rounded-full ios-liquid-pill"
          style={
            isPlayingOrBuffering
              ? {
                  borderColor: 'rgba(var(--dynamic-accent-rgb), 0.36)',
                  boxShadow:
                    'inset 0 1px 0 rgba(255,255,255,0.24), 0 0 18px rgba(var(--dynamic-accent-rgb), 0.14)',
                }
              : undefined
          }
        >
          <button
            onClick={onToggleShuffle}
            title={queue?.shuffle ? 'Shuffle On' : 'Shuffle Off'}
            className={`hidden sm:flex w-7 h-7 rounded-full items-center justify-center transition-colors ${
              queue?.shuffle ? 'bg-white/12' : 'text-[#9BA4C4] hover:text-white'
            }`}
            style={
              queue?.shuffle
                ? {
                    color: 'var(--dynamic-accent)',
                    background: 'rgba(var(--dynamic-accent-rgb), 0.16)',
                  }
                : undefined
            }
          >
            <Shuffle size={13} />
          </button>

          <button
            onClick={onPrev}
            title="Previous Track"
            className="w-7 h-7 rounded-full flex items-center justify-center text-[#D4DCFA] hover:text-white hover:bg-white/10 transition-colors"
          >
            <SkipBack size={14} />
          </button>

          <button
            onClick={onTogglePlay}
            title={playback?.state === 'buffering' ? 'Buffering...' : isPlayingOrBuffering ? 'Pause' : 'Play'}
            className={`relative w-8 h-8 sm:w-9 sm:h-9 ios-liquid-orb text-[#020308] flex items-center justify-center hover:scale-105 active:scale-95 eh-play-morph ${
              isActuallyPlaying ? 'rounded-[11px]' : 'rounded-[20px]'
            }`}
          >
            {playback?.state === 'buffering' && (
              <span
                className="pointer-events-none absolute -inset-[3px] rounded-full border-2 border-transparent eh-buffer-ring"
                style={{
                  borderTopColor: 'var(--dynamic-accent)',
                  borderRightColor: 'rgba(var(--dynamic-accent-rgb), 0.45)',
                }}
              />
            )}
            {playback?.state === 'buffering' ? (
              <Loader2 size={15} className="eh-buffer-ring" />
            ) : isPlayingOrBuffering ? (
              <Pause size={15} className="fill-current" />
            ) : (
              <Play size={15} className="fill-current ml-0.5" />
            )}
          </button>

          <button
            onClick={onNext}
            title="Next Track"
            className="w-7 h-7 rounded-full flex items-center justify-center text-[#D4DCFA] hover:text-white hover:bg-white/10 transition-colors"
          >
            <SkipForward size={14} />
          </button>

          <button
            onClick={onCycleRepeat}
            title={`Repeat: ${queue?.repeat_mode || 'off'}`}
            className={`hidden sm:flex w-7 h-7 rounded-full items-center justify-center transition-colors ${
              queue?.repeat_mode && queue.repeat_mode !== 'off'
                ? 'bg-white/12'
                : 'text-[#9BA4C4] hover:text-white'
            }`}
            style={
              queue?.repeat_mode && queue.repeat_mode !== 'off'
                ? {
                    color: 'var(--dynamic-accent)',
                    background: 'rgba(var(--dynamic-accent-rgb), 0.16)',
                  }
                : undefined
            }
          >
            {queue?.repeat_mode === 'one' ? <Repeat1 size={14} /> : <Repeat size={13} />}
          </button>
        </div>

        {/* GPU-Composited Orbital Scrubber */}
        <div className="hidden sm:flex w-full items-center gap-2 text-[10px] font-mono tabular-nums text-[#9BA4C4]">
          <span ref={elapsedTextRef} className="w-8 text-right flex-none">
            0:00
          </span>

          <div
            ref={progressBarRef}
            onPointerDown={onPointerDown}
            onMouseMove={handleBarHover}
            onMouseLeave={() => setHoverInfo(null)}
            className="flex-1 h-3.5 flex items-center cursor-pointer group relative"
          >
            {hoverInfo && duration > 0 && (
              <div
                style={{ left: `${Math.max(6, Math.min(94, hoverInfo.pct))}%` }}
                className="pointer-events-none absolute -top-7 -translate-x-1/2 px-2 py-0.5 rounded-full ios-liquid-popover text-[10px] font-mono tabular-nums text-[#F4F6FF]"
              >
                {formatTime(hoverInfo.sec)}
              </div>
            )}

            <div className="w-full h-[3.5px] bg-white/[0.15] rounded-full relative group-hover:h-[5px] transition-[height] duration-150">
              <div className="absolute inset-0 rounded-full overflow-hidden">
                <div
                  ref={scrubberFillRef}
                  className="w-full h-full origin-left will-change-transform"
                  style={{
                    transform: 'scaleX(0)',
                    background:
                      'linear-gradient(90deg, var(--dynamic-secondary), var(--dynamic-accent), #FFFFFF)',
                    boxShadow: '0 0 10px rgba(var(--dynamic-accent-rgb), 0.55)',
                  }}
                />
              </div>
              <div className="pointer-events-none absolute inset-0">
                <div
                  ref={scrubberKnobTrackRef}
                  className="w-full h-full relative will-change-transform"
                  style={{ transform: 'translate3d(-100%, 0, 0)' }}
                >
                  <span
                    className="w-3 h-3 rounded-full bg-white absolute right-0 top-1/2 translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity"
                    style={{ boxShadow: '0 0 12px var(--dynamic-accent)' }}
                  />
                </div>
              </div>
            </div>
          </div>

          <span className="w-8 text-left flex-none">{formatTime(duration)}</span>
        </div>
      </div>

      {/* Right: Utility Cluster (Lyrics, Fullscreen Stage, EQ, Download, Volume, Queue) */}
      <div className="flex items-center gap-1 lg:gap-1.5 w-auto md:w-[255px] lg:w-[295px] justify-end relative flex-none">
        <button
          onClick={onToggleLyrics}
          title="Toggle Docked Lyrics Panel"
          className={`w-8 h-8 rounded-full flex items-center justify-center transition-all border ${
            isLyricsOpen
              ? 'ios-liquid-pill border-[rgba(var(--dynamic-accent-rgb),0.55)]'
              : 'border-transparent text-[#9AA5C4] hover:text-white hover:bg-white/[0.08]'
          }`}
          style={isLyricsOpen ? { color: 'var(--dynamic-accent)' } : undefined}
        >
          <AlignLeft size={14} />
        </button>

        <button
          onClick={onToggleFullscreen}
          title="Toggle Fullscreen Stage"
          className={`hidden sm:flex w-8 h-8 rounded-full items-center justify-center transition-all border ${
            isFullscreenOpen
              ? 'ios-liquid-pill border-[rgba(var(--dynamic-accent-rgb),0.55)]'
              : 'border-transparent text-[#9AA5C4] hover:text-white hover:bg-white/[0.08]'
          }`}
          style={isFullscreenOpen ? { color: 'var(--dynamic-accent)' } : undefined}
        >
          <Maximize2 size={13} />
        </button>

        <div className="relative hidden sm:block">
          <button
            onClick={() => setIsEqOpen((o) => !o)}
            title={`Audio EQ: ${eqPreset}`}
            className={`h-8 px-2 rounded-full flex items-center gap-1 transition-all border ${
              isEqOpen || eqPreset !== 'flat'
                ? 'ios-liquid-pill border-[rgba(var(--dynamic-accent-rgb),0.45)]'
                : 'border-transparent text-[#9AA5C4] hover:text-white hover:bg-white/[0.08]'
            }`}
            style={eqPreset !== 'flat' ? { color: 'var(--dynamic-accent)' } : undefined}
          >
            <SlidersHorizontal size={13} />
            {eqPreset !== 'flat' && (
              <span className="hidden lg:inline text-[9px] font-mono uppercase">{eqPreset}</span>
            )}
          </button>

          {isEqOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setIsEqOpen(false)} />
              <div className="absolute bottom-12 right-0 w-52 rounded-2xl ios-liquid-popover eh-popover-in p-2 z-50 space-y-1">
                <div className="px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-[#9AA5C4]">
                  Audio EQ Preset
                </div>
                {EQ_OPTIONS.map((opt) => {
                  const active = eqPreset === opt.id;
                  return (
                    <button
                      key={opt.id}
                      onClick={() => {
                        onChangeEqPreset(opt.id);
                        setIsEqOpen(false);
                      }}
                      className={`w-full text-left px-2.5 py-1.5 rounded-xl transition-colors ${
                        active
                          ? 'bg-white/12 text-[#F1F4FA]'
                          : 'text-[#9AA5C4] hover:bg-white/[0.07] hover:text-white'
                      }`}
                    >
                      <div
                        className="text-xs font-semibold"
                        style={active ? { color: 'var(--dynamic-accent)' } : undefined}
                      >
                        {opt.label}
                      </div>
                      <div className="text-[10px] text-[#7A84A6]">{opt.desc}</div>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>

        {currentTrack && (
          <button
            onClick={() => !isDownloading && !isDownloaded && onDownload(currentTrack)}
            disabled={isDownloading || isDownloaded}
            title={
              isDownloaded
                ? 'Downloaded offline (+ .lrc lyrics)'
                : isDownloading
                ? 'Downloading...'
                : 'Download track & cache synced lyrics'
            }
            className={`hidden md:flex w-8 h-8 rounded-full items-center justify-center transition-colors ${
              isDownloaded
                ? 'text-[#34D399] cursor-default'
                : 'text-[#9AA5C4] hover:text-[#34D399] hover:bg-white/[0.08]'
            }`}
          >
            {isDownloaded ? (
              <CheckCircle2 size={15} className="text-[#34D399]" />
            ) : isDownloading ? (
              <Loader2 size={15} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
            ) : (
              <Download size={14} />
            )}
          </button>
        )}

        <div className="hidden md:flex items-center gap-1.5 px-2 py-1 rounded-full bg-white/[0.06] border border-white/[0.10]">
          <button
            onClick={toggleMute}
            title={volume === 0 ? 'Unmute' : 'Mute'}
            className="p-0.5 text-[#9AA5C4] hover:text-white transition-colors"
          >
            {volume === 0 ? <VolumeX size={14} /> : <Volume2 size={14} />}
          </button>
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            onChange={(e) => handleVolumeChange(Number(e.target.value))}
            aria-label="Volume"
            className="orbit-slider w-12 lg:w-16 cursor-pointer"
          />
        </div>

        <button
          onClick={onToggleQueue}
          title="Toggle Queue Drawer"
          className={`flex items-center gap-1.5 h-8 px-2 sm:px-2.5 rounded-full text-xs transition-all border ${
            isQueueOpen
              ? 'ios-liquid-pill text-[#F1F4FA] border-[rgba(var(--dynamic-accent-rgb),0.55)] font-semibold'
              : 'bg-white/[0.06] text-[#9AA5C4] hover:text-white hover:bg-white/[0.10] border-white/[0.10]'
          }`}
        >
          <ListMusic size={14} style={isQueueOpen ? { color: 'var(--dynamic-accent)' } : undefined} />
          {queueCount > 0 && (
            <span className="hidden sm:inline text-[10px] font-mono tabular-nums">{queueCount}</span>
          )}
        </button>
      </div>
    </footer>
  );
};
