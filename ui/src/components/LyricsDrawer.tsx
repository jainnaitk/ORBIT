import { useEffect, useState, useRef, useCallback } from 'react';
import {
  X,
  AlignLeft,
  Loader2,
  RotateCcw,
  Search,
  RefreshCw,
  HardDrive,
  Heart,
  Download,
  CheckCircle2,
  Maximize2,
  ArrowDown,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import { bridgeApi, bridgeSocket } from '../api/client';
import { useTwoFingerClose } from '../utils/gestures';
import type { PlaybackTick, QueueData, LyricLine, Track, DownloadProgress } from '../api/types';

interface LyricsDrawerProps {
  isOpen: boolean;
  playback: PlaybackTick | null;
  queue: QueueData | null;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  onClose: () => void;
  onExpandFullscreen: () => void;
  onOpenAlbum?: (track: Track) => void;
  onOpenArtist?: (track: Track) => void;
  onSeek: (seconds: number) => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
}

export const LyricsDrawer = ({
  isOpen,
  playback,
  queue,
  downloads = {},
  likedIds,
  downloadedIds,
  onClose,
  onExpandFullscreen,
  onOpenAlbum,
  onOpenArtist,
  onSeek,
  onToggleLike,
  onDownload,
}: LyricsDrawerProps) => {
  const [rendered, setRendered] = useState<boolean>(isOpen);
  const [visible, setVisible] = useState<boolean>(false);
  const [syncedLines, setSyncedLines] = useState<LyricLine[] | null>(null);
  const [plainLyrics, setPlainLyrics] = useState<string | null>(null);
  const [lyricsSource, setLyricsSource] = useState<string | null>(null);
  const [isOfflineLrc, setIsOfflineLrc] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [syncOffset, setSyncOffset] = useState<number>(0);
  const [activeLineIdx, setActiveLineIdx] = useState<number>(0);
  const [showSearch, setShowSearch] = useState<boolean>(false);
  const [manualQuery, setManualQuery] = useState<string>('');
  const [isAutoScrollPaused, setIsAutoScrollPaused] = useState<boolean>(false);

  const asideRef = useRef<HTMLElement>(null);
  const scrollBoxRef = useRef<HTMLDivElement>(null);

  useTwoFingerClose({
    enabled: isOpen && rendered,
    ready: visible && !loading,
    settleMs: 420,
    containerRef: asideRef,
    onClose,
  });
  const lineRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const lastServerPosRef = useRef<number>(0);
  const lastTickRef = useRef<number>(performance.now());
  const isAutoScrollPausedRef = useRef<boolean>(false);
  isAutoScrollPausedRef.current = isAutoScrollPaused;
  const reqSeqRef = useRef<number>(0);
  const scrollAnimRef = useRef<number | null>(null);

  const currentTrack = playback?.current_track || queue?.current_track || null;
  const currentVideoId = currentTrack?.video_id ?? null;
  const isPlaying = playback?.state === 'playing';

  const currentTrackRef = useRef<Track | null>(currentTrack);
  currentTrackRef.current = currentTrack;

  useEffect(() => {
    if (isOpen) {
      setRendered(true);
      const raf1 = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(raf1);
    } else {
      setVisible(false);
      setRendered(false);
    }
  }, [isOpen]);

  // Reset local time anchor cleanly on track switch or drawer open
  useEffect(() => {
    const syncedPos =
      currentVideoId && bridgeSocket.latestTrackId === currentVideoId
        ? bridgeSocket.latestTimePos
        : playback?.time_pos ?? 0;
    lastServerPosRef.current = syncedPos;
    lastTickRef.current = performance.now();
    lineRefs.current = [];
    setIsAutoScrollPaused(false);
    isAutoScrollPausedRef.current = false;
  }, [currentVideoId, isOpen]);

  useEffect(() => {
    if (playback?.time_pos !== undefined) {
      const syncedPos =
        currentVideoId && bridgeSocket.latestTrackId === currentVideoId
          ? bridgeSocket.latestTimePos
          : playback.time_pos;
      lastServerPosRef.current = syncedPos;
      lastTickRef.current = performance.now();
    }
  }, [playback?.time_pos, currentVideoId]);

  const syncedLinesRef = useRef<LyricLine[] | null>(null);
  syncedLinesRef.current = syncedLines;
  const syncOffsetRef = useRef<number>(syncOffset);
  syncOffsetRef.current = syncOffset;
  const isPlayingRef = useRef<boolean>(isPlaying);
  isPlayingRef.current = isPlaying;

  const updateActiveLine = useCallback(() => {
    const lines = syncedLinesRef.current;
    if (!lines || lines.length === 0 || document.hidden) return;
    const elapsed = isPlayingRef.current ? (performance.now() - lastTickRef.current) / 1000 : 0;
    const nowSec = lastServerPosRef.current + elapsed + syncOffsetRef.current;

    let idx = 0;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].time <= nowSec + 0.16) {
        idx = i;
      } else {
        break;
      }
    }
    setActiveLineIdx((prev) => (prev !== idx ? idx : prev));
  }, []);

  // Subscribe directly to live playback_tick updates while Lyrics is open (also syncs seeks while paused)
  useEffect(() => {
    if (!isOpen) return;
    return bridgeSocket.on('playback_tick', (tick) => {
      if (typeof tick.time_pos === 'number') {
        lastServerPosRef.current = tick.time_pos;
        lastTickRef.current = performance.now();
        updateActiveLine();
      }
    });
  }, [isOpen, updateActiveLine]);

  const fetchLyrics = useCallback(
    async (customQ?: string) => {
      const trk = currentTrackRef.current;
      if (!currentVideoId || !trk) return;

      const mySeq = ++reqSeqRef.current;
      setLoading(true);
      setSyncedLines(null);
      setPlainLyrics(null);
      setLyricsSource(null);
      setIsOfflineLrc(false);
      setActiveLineIdx(0);
      setIsAutoScrollPaused(false);
      isAutoScrollPausedRef.current = false;
      lineRefs.current = [];

      try {
        const res = await bridgeApi.getLyrics(
          currentVideoId,
          trk.title,
          trk.artist,
          trk.duration_s,
          customQ
        );
        if (mySeq !== reqSeqRef.current) return;
        setSyncedLines(res.synced && res.synced.length > 0 ? res.synced : null);
        setPlainLyrics(res.lyrics || null);
        setLyricsSource(res.source || null);
        setIsOfflineLrc(Boolean(res.offline));
      } catch {
        if (mySeq !== reqSeqRef.current) return;
        setSyncedLines(null);
        setPlainLyrics(null);
      } finally {
        if (mySeq === reqSeqRef.current) setLoading(false);
      }
    },
    [currentVideoId]
  );

  useEffect(() => {
    if (!isOpen || !currentVideoId) return;
    setSyncOffset(0);
    setShowSearch(false);
    setManualQuery(
      (currentTrackRef.current?.title || '').replace(/\s*[\(\[].*?[\)\]]/g, '').trim()
    );
    void fetchLyrics();
  }, [isOpen, currentVideoId, fetchLyrics]);

  // Real-time sync tracker locked to libmpv live timestamp (polls only while actively playing)
  useEffect(() => {
    if (!isOpen || !syncedLines || syncedLines.length === 0) return;

    updateActiveLine();
    if (!isPlaying) return;
    const interval = window.setInterval(updateActiveLine, 75);
    return () => clearInterval(interval);
  }, [isOpen, syncedLines, isPlaying, syncOffset, playback?.time_pos, updateActiveLine]);

  const scrollToLine = useCallback((idx: number) => {
    const box = scrollBoxRef.current;
    const el = lineRefs.current[idx];
    if (!box || !el) return;

    const boxRect = box.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    const targetTop = Math.max(
      0,
      box.scrollTop + (elRect.top - boxRect.top) - box.clientHeight * 0.44 + elRect.height * 0.5
    );

    if (scrollAnimRef.current !== null) {
      cancelAnimationFrame(scrollAnimRef.current);
    }

    const startTop = box.scrollTop;
    const delta = targetTop - startTop;
    if (Math.abs(delta) < 1) return;

    const duration = 460;
    const startTime = performance.now();

    const step = (now: number) => {
      const t = Math.min(1, (now - startTime) / duration);
      const ease = 1 - Math.pow(1 - t, 4);
      box.scrollTop = startTop + delta * ease;
      if (t < 1) {
        scrollAnimRef.current = requestAnimationFrame(step);
      } else {
        scrollAnimRef.current = null;
      }
    };

    scrollAnimRef.current = requestAnimationFrame(step);
  }, []);

  // Exact pixel vertical centering with smooth Apple-Music quartic easing
  useEffect(() => {
    if (activeLineIdx < 0 || !syncedLines?.length) return;
    if (isAutoScrollPausedRef.current) return;

    scrollToLine(activeLineIdx);
    return () => {
      if (scrollAnimRef.current !== null) {
        cancelAnimationFrame(scrollAnimRef.current);
        scrollAnimRef.current = null;
      }
    };
  }, [activeLineIdx, syncedLines, scrollToLine]);

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

  const plainLines = plainLyrics ? plainLyrics.split(/\r?\n/) : [];

  const handleManualSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualQuery.trim()) return;
    void fetchLyrics(manualQuery.trim());
  };

  const handleResumeAutoScroll = () => {
    isAutoScrollPausedRef.current = false;
    setIsAutoScrollPaused(false);
    scrollToLine(activeLineIdx);
  };

  return (
    <aside
      ref={asideRef}
      data-testid="lyrics-drawer"
      style={{
        transform: visible ? 'translate3d(0, 0, 0)' : 'translate3d(24px, 0, 0)',
        opacity: visible ? 1 : 0,
        transition: 'transform 220ms cubic-bezier(0.22, 1, 0.36, 1), opacity 180ms ease',
      }}
      className="absolute inset-2 z-30 md:static md:inset-auto md:w-[360px] md:flex-none ios-liquid-drawer rounded-[24px] md:my-1 flex flex-col select-none overflow-hidden"
    >
      {/* Header */}
      <div className="h-12 flex-none px-4 flex items-center justify-between border-b border-white/[0.10] bg-white/[0.025]">
        <div className="flex items-center gap-2 min-w-0">
          <AlignLeft size={14} style={{ color: 'var(--dynamic-accent)' }} className="flex-none" />
          <span className="text-xs font-bold uppercase tracking-[0.18em] text-[#F4F6FF]">
            Lyrics
          </span>
          {syncedLines && (
            <span
              className="text-[9px] font-mono uppercase px-1.5 py-0.5 rounded-full border flex-none"
              style={{
                color: 'var(--dynamic-accent)',
                borderColor: 'rgba(var(--dynamic-accent-rgb), 0.45)',
                background: 'rgba(var(--dynamic-accent-rgb), 0.14)',
              }}
            >
              Synced
            </span>
          )}
          {isOfflineLrc && (
            <span className="inline-flex items-center gap-1 text-[9px] font-mono uppercase px-1.5 py-0.5 rounded-full bg-[#34D399]/15 text-[#34D399] border border-[#34D399]/35 flex-none">
              <HardDrive size={9} />
              .lrc
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 flex-none">
          <button
            type="button"
            onClick={() => setShowSearch((s) => !s)}
            title="Find Lyrics Match"
            className="p-1.5 text-[#A6B0CF] hover:text-white hover:bg-white/[0.10] rounded-full transition-colors"
          >
            <Search size={13} />
          </button>
          <button
            type="button"
            onClick={() => void fetchLyrics()}
            disabled={loading || !currentTrack}
            title="Reload Lyrics"
            className="p-1.5 text-[#A6B0CF] hover:text-white hover:bg-white/[0.10] disabled:opacity-35 rounded-full transition-colors"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            type="button"
            onClick={onExpandFullscreen}
            title="Expand to Fullscreen Stage"
            className="p-1.5 text-[#A6B0CF] hover:text-white hover:bg-white/[0.10] rounded-full transition-colors"
          >
            <Maximize2 size={13} />
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Close Lyrics (Esc)"
            className="p-1.5 text-[#A6B0CF] hover:text-white hover:bg-white/[0.10] rounded-full transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Compact Track Ribbon + Sync Offset */}
      {currentTrack && (
        <div className="flex-none px-4 py-2.5 border-b border-white/[0.08] bg-white/[0.02] flex items-center justify-between gap-2.5">
          <div className="flex items-center gap-2.5 min-w-0 flex-1">
            <button
              type="button"
              onClick={() => onOpenAlbum?.(currentTrack)}
              title="Open Album"
              className="w-9 h-9 rounded-lg overflow-hidden border border-white/15 bg-[#0C101C] flex-none hover:scale-105 transition-transform"
            >
              <TrackImage
                src={currentTrack.thumbnail_url}
                videoId={currentTrack.video_id}
                size="sm"
                iconSize={16}
                eager
              />
            </button>
            <div className="min-w-0 flex-1">
              <button
                type="button"
                onClick={onExpandFullscreen}
                title="Open Fullscreen Lyrics"
                className="block text-left text-xs font-semibold text-white truncate max-w-full hover:underline"
              >
                {currentTrack.title}
              </button>
              <button
                type="button"
                onClick={() => onOpenArtist?.(currentTrack)}
                title={`Open ${currentTrack.artist}`}
                className="block text-left text-[10px] text-[#9AA5C4] hover:text-white hover:underline truncate max-w-full"
              >
                {currentTrack.artist}
              </button>
            </div>
          </div>
          <div className="flex items-center gap-1 flex-none">
            <button
              type="button"
              onClick={() => onToggleLike(currentTrack)}
              title={isLiked ? 'Unlike' : 'Like'}
              className={`w-7 h-7 rounded-full flex items-center justify-center transition-colors ${
                isLiked
                  ? 'bg-[#FF4D8D]/16 text-[#FF4D8D]'
                  : 'text-[#A6B0CF] hover:text-white hover:bg-white/[0.08]'
              }`}
            >
              <Heart size={13} className={isLiked ? 'fill-[#FF4D8D]' : ''} />
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
              className={`w-7 h-7 rounded-full flex items-center justify-center transition-colors ${
                isDownloaded
                  ? 'bg-[#34D399]/16 text-[#34D399] cursor-default'
                  : isDownloading
                  ? 'text-[#A6B0CF] cursor-wait'
                  : 'text-[#A6B0CF] hover:text-white hover:bg-white/[0.08]'
              }`}
            >
              {isDownloaded ? (
                <CheckCircle2 size={13} />
              ) : isDownloading ? (
                <Loader2 size={13} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
              ) : (
                <Download size={13} />
              )}
            </button>
          </div>
        </div>
      )}

      {syncedLines && syncedLines.length > 0 && (
        <div
          className="flex-none px-4 py-1.5 border-b border-white/[0.06] bg-black/20 flex items-center justify-between text-[10px]"
          title="Adjust lyrics timing offset"
        >
          <span className="text-[#8E99BA] uppercase tracking-wider text-[9px]">
            Timing Offset
          </span>
          <div className="flex items-center gap-1">
            <span className="text-[#E6ECF8] mr-1 font-mono tabular-nums">
              {syncOffset >= 0 ? `+${syncOffset.toFixed(1)}s` : `${syncOffset.toFixed(1)}s`}
            </span>
            <button
              type="button"
              onClick={() => setSyncOffset((o) => Number((o - 0.5).toFixed(1)))}
              title="Show lyrics 0.5s earlier"
              className="px-1.5 py-0.5 rounded-full bg-white/[0.08] hover:bg-white/[0.18] text-[#F4F6FF] font-mono transition-colors"
            >
              -0.5s
            </button>
            <button
              type="button"
              onClick={() => setSyncOffset((o) => Number((o + 0.5).toFixed(1)))}
              title="Show lyrics 0.5s later"
              className="px-1.5 py-0.5 rounded-full bg-white/[0.08] hover:bg-white/[0.18] text-[#F4F6FF] font-mono transition-colors"
            >
              +0.5s
            </button>
            {syncOffset !== 0 && (
              <button
                type="button"
                onClick={() => setSyncOffset(0)}
                title="Reset sync offset"
                className="p-1 rounded-full bg-white/[0.08] hover:bg-white/[0.18] text-[#A6B0CF] hover:text-white transition-colors"
              >
                <RotateCcw size={10} />
              </button>
            )}
          </div>
        </div>
      )}

      {showSearch && (
        <form
          onSubmit={handleManualSubmit}
          className="eh-popover-in flex-none px-4 py-2 border-b border-white/[0.08] bg-black/25 flex items-center gap-2"
        >
          <input
            type="text"
            value={manualQuery}
            onChange={(e) => setManualQuery(e.target.value)}
            placeholder="Search song & artist on LRCLIB..."
            className="flex-1 min-w-0 bg-black/45 border border-white/15 rounded-full px-3 py-1 text-xs text-[#F4F6FF] placeholder-[#646E8F] focus:outline-none focus:border-white/35"
          />
          <button
            type="submit"
            className="px-3 py-1 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90 flex-none"
          >
            Find
          </button>
        </form>
      )}

      {/* Docked Scrolling Lyrics Canvas + Floating Auto Scroll Pill */}
      <div className="flex-1 min-h-0 relative overflow-hidden">
        <div
          ref={scrollBoxRef}
          onWheel={() => {
            if (syncedLines && syncedLines.length > 0) {
              isAutoScrollPausedRef.current = true;
              setIsAutoScrollPaused(true);
            }
            if (scrollAnimRef.current !== null) {
              cancelAnimationFrame(scrollAnimRef.current);
              scrollAnimRef.current = null;
            }
          }}
          onTouchMove={() => {
            if (syncedLines && syncedLines.length > 0) {
              isAutoScrollPausedRef.current = true;
              setIsAutoScrollPaused(true);
            }
          }}
          className="h-full overflow-y-auto no-scrollbar px-5 lyrics-fade-mask"
        >
          {!currentTrack ? (
            <div className="h-full flex items-center justify-center text-xs text-[#646E8F] text-center">
              Play any track to view synced lyrics.
            </div>
          ) : loading ? (
            <div className="h-full flex flex-col items-center justify-center gap-2.5 text-[#A6B0CF]">
              <Loader2 size={20} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
              <span className="text-xs">Syncing lyrics...</span>
            </div>
          ) : syncedLines && syncedLines.length > 0 ? (
            <div className="space-y-3.5" style={{ paddingTop: '36%', paddingBottom: '48%' }}>
              {syncedLines.map((line, i) => {
                const dist = Math.abs(i - activeLineIdx);
                const isActive = i === activeLineIdx;
                const isPast = i < activeLineIdx;

                const opacity = isActive
                  ? 1
                  : dist === 1
                  ? isPast
                    ? 0.44
                    : 0.65
                  : dist === 2
                  ? 0.34
                  : 0.22;

                return (
                  <button
                    key={`${line.time}-${i}`}
                    ref={(el) => {
                      lineRefs.current[i] = el;
                    }}
                    type="button"
                    onClick={() => {
                      isAutoScrollPausedRef.current = false;
                      setIsAutoScrollPaused(false);
                      const target = Math.max(0, line.time - syncOffset);
                      lastServerPosRef.current = target;
                      lastTickRef.current = performance.now();
                      setActiveLineIdx(i);
                      onSeek(target);
                    }}
                    style={{
                      opacity,
                      transform: isActive
                        ? 'translate3d(4px, 0, 0) scale(1.04)'
                        : 'translate3d(0, 0, 0) scale(0.95)',
                      transformOrigin: 'left center',
                      transition:
                        'transform 260ms cubic-bezier(0.22, 1, 0.36, 1), opacity 220ms ease',
                      willChange: isActive || dist <= 2 ? 'transform, opacity' : 'auto',
                    }}
                    className={`block w-full text-left rounded-xl px-1.5 py-0.5 leading-snug text-[17px] font-bold tracking-[-0.012em] hover:!opacity-100 ${
                      isActive ? 'text-white' : 'text-[#D6DDF5]'
                    }`}
                  >
                    {line.text || '♪'}
                  </button>
                );
              })}
              {lyricsSource && (
                <p className="pt-6 text-[10px] font-mono text-[#646E8F]">
                  Synced via {lyricsSource}
                </p>
              )}
            </div>
          ) : plainLines.length > 0 ? (
            <div className="space-y-2.5 py-8">
              {plainLines.map((line, i) => (
                <p
                  key={i}
                  className={
                    line.trim()
                      ? 'text-sm leading-relaxed font-medium text-[#F4F6FF]/85'
                      : 'h-2.5'
                  }
                >
                  {line}
                </p>
              ))}
              {lyricsSource && (
                <p className="pt-5 text-[10px] font-mono text-[#646E8F] border-t border-white/[0.08]">
                  Source: {lyricsSource}
                </p>
              )}
            </div>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-center text-[#A6B0CF] gap-3 px-2">
              <AlignLeft size={26} className="opacity-30" />
              <div>
                <p className="text-xs font-semibold text-[#F4F6FF]">No lyrics found automatically</p>
                <p className="text-[11px] text-[#646E8F] mt-1">
                  Search the original song name below.
                </p>
              </div>
              <form onSubmit={handleManualSubmit} className="flex items-center gap-1.5 w-full">
                <input
                  type="text"
                  value={manualQuery}
                  onChange={(e) => setManualQuery(e.target.value)}
                  placeholder="Search song name..."
                  className="flex-1 min-w-0 bg-black/45 border border-white/15 rounded-full px-3 py-1.5 text-xs text-[#F4F6FF] placeholder-[#646E8F] focus:outline-none focus:border-white/35"
                />
                <button
                  type="submit"
                  className="px-3.5 py-1.5 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] flex-none"
                >
                  Find
                </button>
              </form>
            </div>
          )}
        </div>

        {/* Floating Auto scroll pill */}
        {isAutoScrollPaused && syncedLines && syncedLines.length > 0 && (
          <button
            type="button"
            onClick={handleResumeAutoScroll}
            className="eh-popover-in absolute bottom-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-1.5 px-3.5 py-1.5 rounded-full ios-liquid-glass text-xs font-semibold text-white hover:scale-105 active:scale-95 transition-transform"
            style={{
              borderColor: 'rgba(var(--dynamic-accent-rgb), 0.55)',
              boxShadow: '0 8px 24px rgba(0,0,0,0.65), 0 0 18px rgba(var(--dynamic-accent-rgb), 0.22)',
            }}
          >
            <ArrowDown size={13} style={{ color: 'var(--dynamic-accent)' }} />
            <span>Auto scroll</span>
          </button>
        )}
      </div>
    </aside>
  );
};
