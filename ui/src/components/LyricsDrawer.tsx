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
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import { bridgeApi, bridgeSocket } from '../api/client';
import type { PlaybackTick, QueueData, LyricLine, Track, DownloadProgress } from '../api/types';

interface LyricsDrawerProps {
  isOpen: boolean;
  playback: PlaybackTick | null;
  queue: QueueData | null;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  onClose: () => void;
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

  const scrollBoxRef = useRef<HTMLDivElement>(null);
  const lineRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const lastServerPosRef = useRef<number>(0);
  const lastTickRef = useRef<number>(performance.now());
  const userScrollUntilRef = useRef<number>(0);
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
      const t = window.setTimeout(() => setRendered(false), 280);
      return () => clearTimeout(t);
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

  // Exact pixel vertical centering with smooth Apple-Music quartic easing
  useEffect(() => {
    if (activeLineIdx < 0 || !syncedLines?.length) return;
    if (performance.now() < userScrollUntilRef.current) return;

    const box = scrollBoxRef.current;
    const el = lineRefs.current[activeLineIdx];
    if (!box || !el) return;

    const boxRect = box.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    const targetTop = Math.max(
      0,
      box.scrollTop + (elRect.top - boxRect.top) - box.clientHeight * 0.45 + elRect.height * 0.5
    );

    if (scrollAnimRef.current !== null) {
      cancelAnimationFrame(scrollAnimRef.current);
    }

    const startTop = box.scrollTop;
    const delta = targetTop - startTop;
    if (Math.abs(delta) < 1) return;

    const duration = 480;
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
    return () => {
      if (scrollAnimRef.current !== null) {
        cancelAnimationFrame(scrollAnimRef.current);
        scrollAnimRef.current = null;
      }
    };
  }, [activeLineIdx, syncedLines]);

  if (!rendered) return null;

  const isLiked = currentTrack
    ? likedIds
      ? likedIds.has(currentTrack.video_id)
      : !!currentTrack.is_liked
    : false;
  const isDownloaded =
    !!currentTrack &&
    (downloadedIds?.has(currentTrack.video_id) ||
      currentTrack.download_status === 'completed' ||
      downloads[currentTrack.video_id]?.status === 'completed' ||
      !!currentTrack.local_path);

  const plainLines = plainLyrics ? plainLyrics.split(/\r?\n/) : [];

  const handleManualSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualQuery.trim()) return;
    void fetchLyrics(manualQuery.trim());
  };

  return (
    <aside
      data-testid="lyrics-drawer"
      style={{
        transform: visible ? 'translate3d(0, 0, 0) scale(1)' : 'translate3d(0, 16px, 0) scale(0.985)',
        opacity: visible ? 1 : 0,
        transition: 'transform 280ms cubic-bezier(0.22, 1, 0.36, 1), opacity 220ms ease',
        willChange: 'transform, opacity',
      }}
      className="absolute inset-0 z-30 ios-liquid-drawer rounded-[26px] my-1 flex flex-col select-none overflow-hidden"
    >
      <div className="h-12 flex-none px-5 flex items-center justify-between border-b border-white/[0.10] bg-white/[0.025]">
        <div className="flex items-center gap-2">
          <AlignLeft size={14} style={{ color: 'var(--dynamic-accent)' }} />
          <span className="text-xs font-bold uppercase tracking-[0.2em] text-[#F4F6FF]">
            Lyrics
          </span>
          {syncedLines && (
            <span
              className="text-[9px] font-mono uppercase px-2 py-0.5 rounded-full border"
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
            <span className="inline-flex items-center gap-1 text-[9px] font-mono uppercase px-2 py-0.5 rounded-full bg-[#34D399]/15 text-[#34D399] border border-[#34D399]/35">
              <HardDrive size={9} />
              Offline .lrc
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {syncedLines && syncedLines.length > 0 && (
            <div
              className="hidden sm:flex items-center gap-1 mr-2 px-2 py-0.5 rounded-full bg-white/[0.05] border border-white/[0.10] text-[10px]"
              title="Adjust lyrics timing offset"
            >
              <span className="text-[#8E99BA] uppercase tracking-wider text-[9px] mr-0.5">
                Sync
              </span>
              <span className="text-[#E6ECF8] mr-1 font-mono tabular-nums">
                {syncOffset >= 0 ? `+${syncOffset.toFixed(1)}s` : `${syncOffset.toFixed(1)}s`}
              </span>
              <button
                onClick={() => setSyncOffset((o) => Number((o - 0.5).toFixed(1)))}
                title="Show lyrics 0.5s earlier"
                className="px-1.5 py-0.5 rounded-full bg-white/[0.08] hover:bg-white/[0.18] text-[#F4F6FF] font-mono transition-colors"
              >
                -0.5s
              </button>
              <button
                onClick={() => setSyncOffset((o) => Number((o + 0.5).toFixed(1)))}
                title="Show lyrics 0.5s later"
                className="px-1.5 py-0.5 rounded-full bg-white/[0.08] hover:bg-white/[0.18] text-[#F4F6FF] font-mono transition-colors"
              >
                +0.5s
              </button>
              {syncOffset !== 0 && (
                <button
                  onClick={() => setSyncOffset(0)}
                  title="Reset sync offset"
                  className="p-1 rounded-full bg-white/[0.08] hover:bg-white/[0.18] text-[#A6B0CF] hover:text-white transition-colors"
                >
                  <RotateCcw size={10} />
                </button>
              )}
            </div>
          )}

          <button
            onClick={() => setShowSearch((s) => !s)}
            title="Find Lyrics Match"
            className="p-1.5 text-[#A6B0CF] hover:text-white hover:bg-white/[0.10] rounded-full transition-colors"
          >
            <Search size={14} />
          </button>
          <button
            onClick={() => void fetchLyrics()}
            disabled={loading || !currentTrack}
            title="Reload Lyrics"
            className="p-1.5 text-[#A6B0CF] hover:text-white hover:bg-white/[0.10] disabled:opacity-35 rounded-full transition-colors"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={onClose}
            title="Close Lyrics (Esc)"
            className="p-1.5 text-[#A6B0CF] hover:text-white hover:bg-white/[0.10] rounded-full transition-colors"
          >
            <X size={15} />
          </button>
        </div>
      </div>

      {showSearch && (
        <form
          onSubmit={handleManualSubmit}
          className="eh-popover-in flex-none px-5 py-2.5 border-b border-white/[0.08] bg-black/25 flex items-center gap-2"
        >
          <input
            type="text"
            value={manualQuery}
            onChange={(e) => setManualQuery(e.target.value)}
            placeholder="Search song name & artist on LRCLIB..."
            className="flex-1 bg-black/45 border border-white/15 rounded-full px-4 py-1.5 text-xs text-[#F4F6FF] placeholder-[#646E8F] focus:outline-none focus:border-white/35"
          />
          <button
            type="submit"
            className="px-4 py-1.5 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90"
          >
            Search
          </button>
        </form>
      )}

      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-12 overflow-hidden">
        <div className="hidden md:flex md:col-span-5 flex-col items-center justify-center p-4 lg:p-6 md:border-r border-white/[0.08] overflow-y-auto no-scrollbar">
          {currentTrack ? (
            <div className="ios-liquid-card rounded-[24px] p-5 w-full max-w-[300px] flex flex-col items-center text-center my-auto">
              <div className="relative w-40 h-40 lg:w-48 lg:h-48 flex items-center justify-center flex-none">
                <div
                  className={`eh-accretion absolute -inset-2.5 rounded-full opacity-45 ${
                    isPlaying ? '' : 'eh-paused opacity-20'
                  }`}
                  style={{
                    background:
                      'conic-gradient(from 0deg, transparent 0%, rgba(var(--dynamic-accent-rgb), 0.55) 30%, rgba(var(--dynamic-secondary-rgb), 0.25) 60%, transparent 85%)',
                    WebkitMask:
                      'radial-gradient(farthest-side, transparent 88%, #000 90%, #000 96%, transparent 100%)',
                    mask: 'radial-gradient(farthest-side, transparent 88%, #000 90%, #000 96%, transparent 100%)',
                  }}
                />
                <div className="relative w-36 h-36 lg:w-44 lg:h-44 rounded-2xl overflow-hidden border border-white/15 shadow-[0_14px_32px_rgba(0,0,0,0.75)] bg-[#0C101C]">
                  <TrackImage
                    src={currentTrack.thumbnail_url}
                    videoId={currentTrack.video_id}
                    size="lg"
                    iconSize={40}
                    eager
                  />
                </div>
              </div>

              <div className="mt-4 w-full space-y-1 px-1">
                <h2
                  title={currentTrack.title}
                  className="text-base lg:text-lg font-bold text-white leading-snug line-clamp-2 break-words"
                >
                  {currentTrack.title || 'Untitled Track'}
                </h2>
                <p
                  title={currentTrack.artist}
                  className="text-xs font-medium text-[#B2BCD9] truncate"
                >
                  {currentTrack.artist}
                </p>
                {currentTrack.album && (
                  <p title={currentTrack.album} className="text-[11px] text-[#7A84A6] truncate">
                    {currentTrack.album}
                  </p>
                )}
              </div>

              <div className="flex items-center gap-2 mt-4">
                <button
                  onClick={() => onToggleLike(currentTrack)}
                  className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                    isLiked
                      ? 'bg-[#FF4D8D]/16 text-[#FF4D8D] border-[#FF4D8D]/40'
                      : 'ios-liquid-pill text-[#C8D1EE] hover:text-white'
                  }`}
                >
                  <Heart size={13} className={isLiked ? 'fill-[#FF4D8D]' : ''} />
                  <span>{isLiked ? 'Liked' : 'Like'}</span>
                </button>
                <button
                  onClick={() => onDownload(currentTrack)}
                  className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                    isDownloaded
                      ? 'bg-[#34D399]/16 text-[#34D399] border-[#34D399]/40'
                      : 'ios-liquid-pill text-[#C8D1EE] hover:text-white'
                  }`}
                >
                  {isDownloaded ? <CheckCircle2 size={13} /> : <Download size={13} />}
                  <span>{isDownloaded ? 'Offline' : 'Download'}</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="text-xs text-[#7A84A6]">Play any track to view synced lyrics.</div>
          )}
        </div>

        <div
          ref={scrollBoxRef}
          onWheel={() => {
            userScrollUntilRef.current = performance.now() + 3600;
            if (scrollAnimRef.current !== null) {
              cancelAnimationFrame(scrollAnimRef.current);
              scrollAnimRef.current = null;
            }
          }}
          className="md:col-span-7 overflow-y-auto no-scrollbar px-6 lg:px-10 lyrics-fade-mask relative"
        >
          {!currentTrack ? (
            <div className="h-full flex items-center justify-center text-xs text-[#646E8F]">
              No active song.
            </div>
          ) : loading ? (
            <div className="h-full flex flex-col items-center justify-center gap-2.5 text-[#A6B0CF]">
              <Loader2 size={22} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
              <span className="text-xs">Syncing lyrics...</span>
            </div>
          ) : syncedLines && syncedLines.length > 0 ? (
            <div className="space-y-4 max-w-xl mx-auto" style={{ paddingTop: '36%', paddingBottom: '44%' }}>
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
                      userScrollUntilRef.current = 0;
                      const target = Math.max(0, line.time - syncOffset);
                      lastServerPosRef.current = target;
                      lastTickRef.current = performance.now();
                      setActiveLineIdx(i);
                      onSeek(target);
                    }}
                    style={{
                      opacity,
                      transform: isActive
                        ? 'translate3d(6px, 0, 0) scale(1.04)'
                        : 'translate3d(0, 0, 0) scale(1)',
                      transformOrigin: 'left center',
                      transition:
                        'transform 400ms cubic-bezier(0.22, 1, 0.36, 1), opacity 340ms ease, text-shadow 340ms ease',
                      textShadow: isActive
                        ? '0 0 18px rgba(var(--dynamic-accent-rgb), 0.35)'
                        : 'none',
                    }}
                    className={`block w-full text-left rounded-xl px-2 py-1 leading-snug hover:!opacity-100 ${
                      isActive
                        ? 'text-2xl sm:text-[26px] font-bold text-white tracking-[-0.015em]'
                        : 'text-lg sm:text-[19px] font-semibold text-[#D6DDF5]'
                    }`}
                  >
                    {line.text || '♪'}
                  </button>
                );
              })}
              {lyricsSource && (
                <p className="pt-8 text-[11px] font-mono text-[#646E8F]">
                  Synced via {lyricsSource}
                </p>
              )}
            </div>
          ) : plainLines.length > 0 ? (
            <div className="space-y-3 py-10 max-w-xl mx-auto">
              {plainLines.map((line, i) => (
                <p
                  key={i}
                  className={
                    line.trim()
                      ? 'text-base leading-relaxed font-medium text-[#F4F6FF]/85'
                      : 'h-3'
                  }
                >
                  {line}
                </p>
              ))}
              {lyricsSource && (
                <p className="pt-6 text-[11px] font-mono text-[#646E8F] border-t border-white/[0.08]">
                  Source: {lyricsSource}
                </p>
              )}
            </div>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-center text-[#A6B0CF] gap-3">
              <AlignLeft size={28} className="opacity-30" />
              <div>
                <p className="text-sm font-semibold text-[#F4F6FF]">No lyrics found automatically</p>
                <p className="text-xs text-[#646E8F] mt-1">
                  Search the original song name below.
                </p>
              </div>
              <form onSubmit={handleManualSubmit} className="flex items-center gap-2 w-full max-w-sm">
                <input
                  type="text"
                  value={manualQuery}
                  onChange={(e) => setManualQuery(e.target.value)}
                  placeholder="Search song name..."
                  className="flex-1 bg-black/45 border border-white/15 rounded-full px-4 py-2 text-xs text-[#F4F6FF] placeholder-[#646E8F] focus:outline-none focus:border-white/35"
                />
                <button
                  type="submit"
                  className="px-4 py-2 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308]"
                >
                  Find
                </button>
              </form>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};
