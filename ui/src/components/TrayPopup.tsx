import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Play,
  Pause,
  Loader2,
  SkipBack,
  SkipForward,
  Shuffle,
  Repeat,
  Repeat1,
  Volume2,
  VolumeX,
  Heart,
  Maximize2,
  PictureInPicture2,
  X,
  Power,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import { OrbitLogo } from './OrbitLogo';
import {
  useBridge,
  bridgeSocket,
  hideTrayPopup,
  openMainFromTray,
  minimizeAppToTray,
  openIslandFromTray,
  quitFromTray,
} from '../api/client';
import { extractTrackAccent, DEFAULT_COSMIC_ACCENT } from '../utils/colorExtract';

function formatTime(secs?: number): string {
  if (!secs || isNaN(secs) || secs < 0) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

export function TrayPopup() {
  const { isConnected, playback, setPlayback, queue, setQueue, api } = useBridge();
  const [likedIds, setLikedIds] = useState<Set<string>>(new Set());

  const barRef = useRef<HTMLDivElement>(null);
  const progressFillRef = useRef<HTMLDivElement>(null);
  const elapsedTextRef = useRef<HTMLSpanElement>(null);
  const lastDisplayedSecRef = useRef<number>(-1);
  const lastServerPosRef = useRef<number>(0);
  const lastTickAtRef = useRef<number>(performance.now());
  const seekGuardUntilRef = useRef<number>(0);
  const seekTargetRef = useRef<number>(0);
  const prevVolumeRef = useRef<number>(80);

  const currentTrack = playback?.current_track || queue?.current_track || null;
  const activeVideoId = currentTrack?.video_id || null;
  const activeThumb = currentTrack?.thumbnail_url || null;
  const isBuffering = playback?.state === 'buffering';
  const isActuallyPlaying = playback?.state === 'playing';
  const isPlaying = isActuallyPlaying || isBuffering;
  const duration = playback?.duration || currentTrack?.duration_s || 0;
  const durationRef = useRef<number>(duration);
  durationRef.current = duration;

  const volume = playback?.volume ?? 80;
  const isMuted = volume <= 0;
  if (volume > 0) {
    prevVolumeRef.current = volume;
  }

  const paintProgress = useCallback(
    (posSec: number, durSec: number, forceText: boolean = false) => {
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
    },
    []
  );

  // Sync dynamic album-art accent CSS variables
  useEffect(() => {
    let mounted = true;
    const applyAccent = (accent = DEFAULT_COSMIC_ACCENT) => {
      const root = document.documentElement;
      root.style.setProperty('--dynamic-accent', accent.hex);
      root.style.setProperty('--dynamic-accent-rgb', accent.rgb);
      root.style.setProperty('--dynamic-secondary', accent.secondaryHex);
      root.style.setProperty('--dynamic-secondary-rgb', accent.secondaryRgb);
    };

    if (!activeVideoId) {
      applyAccent();
      return;
    }

    extractTrackAccent(activeVideoId, activeThumb).then((accent) => {
      if (mounted) applyAccent(accent);
    });

    return () => {
      mounted = false;
    };
  }, [activeVideoId, activeThumb]);

  // Keep liked state synced with backend & main window
  const refreshLiked = useCallback(async () => {
    try {
      const liked = await api.getLiked();
      setLikedIds(new Set(liked.map((t) => t.video_id)));
    } catch {
      // Ignore transient bridge errors
    }
  }, [api]);

  useEffect(() => {
    void refreshLiked();
  }, [refreshLiked, isConnected]);

  useEffect(() => {
    return bridgeSocket.on('library_changed', (evt) => {
      if (!evt?.type || evt.type === 'liked') {
        void refreshLiked();
      }
    });
  }, [refreshLiked]);

  // Anchor progress bar on track / state changes
  useEffect(() => {
    const startPos =
      activeVideoId && bridgeSocket.latestTrackId === activeVideoId
        ? bridgeSocket.latestTimePos
        : playback?.time_pos || 0;
    lastServerPosRef.current = startPos;
    lastTickAtRef.current = performance.now();
    paintProgress(startPos, duration, true);
  }, [playback?.time_pos, activeVideoId, duration, paintProgress]);

  // Direct WebSocket ticks for zero-rerender progress updates
  useEffect(() => {
    const unsubTick = bridgeSocket.on('playback_tick', (tick) => {
      if (typeof tick.time_pos === 'number') {
        const now = performance.now();
        if (
          now < seekGuardUntilRef.current &&
          Math.abs(tick.time_pos - seekTargetRef.current) > 1.5
        ) {
          return;
        }
        lastServerPosRef.current = tick.time_pos;
        lastTickAtRef.current = now;
        paintProgress(tick.time_pos, tick.duration || durationRef.current);
      }
    });
    const unsubTrack = bridgeSocket.on('track_changed', (evt) => {
      const nextPos = typeof evt.time_pos === 'number' ? evt.time_pos : 0;
      const nextDur = typeof evt.duration === 'number' ? evt.duration : durationRef.current;
      seekGuardUntilRef.current = 0;
      lastServerPosRef.current = nextPos;
      lastTickAtRef.current = performance.now();
      paintProgress(nextPos, nextDur, true);
    });
    return () => {
      unsubTick();
      unsubTrack();
    };
  }, [paintProgress]);

  // Smooth RAF progress bar while popup is visible and track is playing
  useEffect(() => {
    if (!isActuallyPlaying) return;
    let rafId = 0;

    const frame = (now: number) => {
      if (document.hidden || !document.hasFocus()) {
        rafId = 0;
        return;
      }
      rafId = requestAnimationFrame(frame);
      const elapsed = (now - lastTickAtRef.current) / 1000;
      const dur = durationRef.current;
      const est = Math.min(dur > 0 ? dur : Infinity, lastServerPosRef.current + elapsed);
      paintProgress(est, dur);
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
  }, [isActuallyPlaying, paintProgress]);

  const handleTogglePlay = useCallback(async () => {
    if (!isPlaying && !playback?.current_track && !queue?.current_track) return;
    const prevState = playback?.state;
    setPlayback((prev) =>
      prev ? { ...prev, state: isPlaying ? 'paused' : 'playing' } : null
    );
    try {
      const tick = isPlaying ? await api.pause() : await api.resume();
      if (tick && typeof tick.state === 'string') {
        setPlayback((prev) => ({
          time_pos: typeof tick.time_pos === 'number' ? tick.time_pos : prev?.time_pos ?? 0,
          duration: typeof tick.duration === 'number' ? tick.duration : prev?.duration ?? 0,
          state: tick.state,
          volume: typeof tick.volume === 'number' ? tick.volume : prev?.volume ?? 80,
          current_track:
            tick.current_track !== undefined ? tick.current_track : prev?.current_track ?? null,
        }));
      }
    } catch {
      if (prevState) {
        setPlayback((prev) => (prev ? { ...prev, state: prevState } : null));
      }
    }
  }, [api, isPlaying, playback?.state, playback?.current_track, queue?.current_track, setPlayback]);

  const handleNext = useCallback(async () => {
    try {
      await api.next();
    } catch {
      // Ignore
    }
  }, [api]);

  const handlePrev = useCallback(async () => {
    try {
      await api.prev();
    } catch {
      // Ignore
    }
  }, [api]);

  const handleToggleShuffle = useCallback(async () => {
    const nextShuffle = !(queue?.shuffle ?? false);
    setQueue((prev) => (prev ? { ...prev, shuffle: nextShuffle } : prev));
    try {
      await api.setShuffle(nextShuffle);
    } catch {
      // Ignore
    }
  }, [api, queue?.shuffle, setQueue]);

  const handleCycleRepeat = useCallback(async () => {
    const modes: ('off' | 'all' | 'one')[] = ['off', 'all', 'one'];
    const current = queue?.repeat_mode || 'off';
    const nextMode = modes[(modes.indexOf(current) + 1) % modes.length];
    setQueue((prev) => (prev ? { ...prev, repeat_mode: nextMode } : prev));
    try {
      await api.setRepeat(nextMode);
    } catch {
      // Ignore
    }
  }, [api, queue?.repeat_mode, setQueue]);

  const handleToggleMute = useCallback(async () => {
    const nextVol = isMuted ? Math.max(10, prevVolumeRef.current || 80) : 0;
    setPlayback((prev) => (prev ? { ...prev, volume: nextVol } : prev));
    try {
      await api.setVolume(nextVol);
    } catch {
      // Ignore
    }
  }, [api, isMuted, setPlayback]);

  const handleToggleLike = useCallback(async () => {
    if (!currentTrack) return;
    const vid = currentTrack.video_id;
    const wasLiked = likedIds.has(vid);
    setLikedIds((prev) => {
      const next = new Set(prev);
      if (wasLiked) next.delete(vid);
      else next.add(vid);
      return next;
    });
    try {
      await api.toggleLike(vid, currentTrack);
    } catch {
      setLikedIds((prev) => {
        const next = new Set(prev);
        if (wasLiked) next.add(vid);
        else next.delete(vid);
        return next;
      });
    }
  }, [api, currentTrack, likedIds]);

  const handleSeekClick = useCallback(
    (e: React.MouseEvent) => {
      if (!barRef.current || duration <= 0) return;
      const rect = barRef.current.getBoundingClientRect();
      const clickRatio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const targetSec = clickRatio * duration;
      const now = performance.now();
      seekTargetRef.current = targetSec;
      seekGuardUntilRef.current = now + 850;
      bridgeSocket.latestTimePos = targetSec;
      bridgeSocket.latestTickAt = now;
      lastServerPosRef.current = targetSec;
      lastTickAtRef.current = now;
      paintProgress(targetSec, duration, true);
      void api.seek(targetSec).catch(() => {});
    },
    [api, duration, paintProgress]
  );

  // Keyboard shortcuts (Escape dismisses popup, Space toggles play/pause)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        void hideTrayPopup();
      } else if (e.code === 'Space') {
        e.preventDefault();
        void handleTogglePlay();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handleTogglePlay]);

  const isLiked = currentTrack ? likedIds.has(currentTrack.video_id) : false;
  const repeatMode = queue?.repeat_mode || 'off';
  const isShuffle = Boolean(queue?.shuffle);

  return (
    <div
      onContextMenu={(e) => e.preventDefault()}
      className="w-screen h-screen bg-transparent flex items-center justify-center p-1 select-none overflow-hidden"
    >
      <div className="ios-liquid-island eh-island-in relative w-[240px] h-[240px] rounded-[26px] overflow-hidden flex flex-col justify-between p-3">
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

        {/* Dark Obsidian Scrim for Contrast (matches Square Dynamic Island) */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              'linear-gradient(180deg, rgba(5,7,14,0.72) 0%, rgba(5,7,14,0.36) 44%, rgba(5,7,14,0.94) 100%)',
          }}
        />

        {/* Top Bar: Open ORBIT + Dynamic Island | Minimize to Tray (X) + Quit */}
        <div className="relative z-10 flex items-center justify-between gap-1.5">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => void openMainFromTray()}
              title="Open ORBIT"
              className="h-7 px-2.5 rounded-full bg-black/45 hover:bg-black/70 border border-white/20 text-[#DCE3FC] hover:text-white flex items-center gap-1.5 transition-colors"
            >
              <Maximize2 size={11} style={{ color: 'var(--dynamic-accent)' }} />
              <span className="text-[10px] font-semibold tracking-wider uppercase">ORBIT</span>
            </button>
            <button
              type="button"
              onClick={() => void openIslandFromTray()}
              title="Open Dynamic Island"
              className="w-7 h-7 rounded-full bg-black/45 hover:bg-black/70 border border-white/20 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
            >
              <PictureInPicture2 size={12} />
            </button>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => void minimizeAppToTray()}
              title="Minimize app to Tray"
              className="w-7 h-7 rounded-full bg-black/45 hover:bg-black/70 border border-white/20 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
            >
              <X size={12} />
            </button>
            <button
              type="button"
              onClick={() => void quitFromTray()}
              title="Quit ORBIT completely"
              className="w-7 h-7 rounded-full bg-black/45 hover:bg-[#E81123] border border-white/20 hover:border-[#FF4D8D]/60 text-[#DCE3FC] hover:text-white flex items-center justify-center transition-colors"
            >
              <Power size={12} />
            </button>
          </div>
        </div>

        {/* Centered Transport Controls: Shuffle | Prev | Play/Pause | Next | Repeat */}
        <div className="relative z-10 flex items-center justify-center gap-1.5 my-auto">
          <button
            type="button"
            onClick={() => void handleToggleShuffle()}
            title={isShuffle ? 'Shuffle On' : 'Shuffle Off'}
            className={`w-7 h-7 rounded-full border flex items-center justify-center transition-colors ${
              isShuffle
                ? 'bg-black/65 border-[rgba(var(--dynamic-accent-rgb),0.65)]'
                : 'bg-black/40 hover:bg-black/65 border-white/15 text-[#C8D1EE] hover:text-white'
            }`}
            style={isShuffle ? { color: 'var(--dynamic-accent)' } : undefined}
          >
            <Shuffle size={12} />
          </button>

          <button
            type="button"
            onClick={() => void handlePrev()}
            title="Previous"
            className="w-8 h-8 rounded-full bg-black/45 hover:bg-black/70 border border-white/15 text-white flex items-center justify-center transition-colors"
          >
            <SkipBack size={15} />
          </button>

          <button
            type="button"
            onClick={() => void handleTogglePlay()}
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
            onClick={() => void handleNext()}
            title="Next"
            className="w-8 h-8 rounded-full bg-black/45 hover:bg-black/70 border border-white/15 text-white flex items-center justify-center transition-colors"
          >
            <SkipForward size={15} />
          </button>

          <button
            type="button"
            onClick={() => void handleCycleRepeat()}
            title={`Repeat: ${repeatMode}`}
            className={`w-7 h-7 rounded-full border flex items-center justify-center transition-colors ${
              repeatMode !== 'off'
                ? 'bg-black/65 border-[rgba(var(--dynamic-accent-rgb),0.65)]'
                : 'bg-black/40 hover:bg-black/65 border-white/15 text-[#C8D1EE] hover:text-white'
            }`}
            style={repeatMode !== 'off' ? { color: 'var(--dynamic-accent)' } : undefined}
          >
            {repeatMode === 'one' ? <Repeat1 size={12} /> : <Repeat size={12} />}
          </button>
        </div>

        {/* Bottom Metadata + Mute + Heart + Clickable Seek Bar */}
        <div className="relative z-10 space-y-1.5">
          <div className="flex items-center justify-between gap-1.5">
            <div className="min-w-0 flex-1">
              <div className="text-xs font-bold text-white truncate leading-tight">
                {currentTrack?.title || 'ORBIT'}
              </div>
              <div className="text-[10px] text-[#C8D1EE] truncate leading-tight mt-0.5">
                {currentTrack?.artist || (isConnected ? 'Ready to play' : 'Connecting...')}
              </div>
            </div>
            <div className="flex items-center gap-0.5 flex-none">
              <button
                type="button"
                onClick={() => void handleToggleMute()}
                title={isMuted ? 'Unmute' : 'Mute'}
                className={`w-7 h-7 rounded-full flex items-center justify-center hover:bg-white/10 transition-colors ${
                  isMuted ? 'text-[#FF4D8D]' : 'text-[#DCE3FC] hover:text-white'
                }`}
              >
                {isMuted ? <VolumeX size={13} /> : <Volume2 size={13} />}
              </button>
              <button
                type="button"
                onClick={() => void handleToggleLike()}
                disabled={!currentTrack}
                title={isLiked ? 'Unlike' : 'Like'}
                className="w-7 h-7 rounded-full flex items-center justify-center text-[#DCE3FC] hover:text-[#FF4D8D] hover:bg-white/10 disabled:opacity-30 transition-colors"
              >
                <Heart size={13} className={isLiked ? 'fill-[#FF4D8D] text-[#FF4D8D]' : ''} />
              </button>
            </div>
          </div>

          <div className="space-y-0.5">
            <div
              ref={barRef}
              onClick={handleSeekClick}
              title="Click to seek"
              className="w-full h-2.5 flex items-center cursor-pointer group"
            >
              <div className="w-full h-[3px] group-hover:h-[4.5px] bg-white/[0.22] rounded-full overflow-hidden transition-[height] duration-150">
                <div
                  ref={progressFillRef}
                  className="w-full h-full rounded-full origin-left will-change-transform"
                  style={{
                    transform: 'scaleX(0)',
                    background:
                      'linear-gradient(90deg, var(--dynamic-secondary), var(--dynamic-accent), #FFFFFF)',
                  }}
                />
              </div>
            </div>
            <div className="flex items-center justify-between text-[9px] font-mono tabular-nums text-[#C8D1EE]/80 leading-none">
              <span ref={elapsedTextRef}>0:00</span>
              <span>{formatTime(duration)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
