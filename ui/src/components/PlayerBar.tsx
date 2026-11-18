import { useEffect, useRef, useState, useCallback } from 'react';
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
  ListMusic,
  Volume2,
  VolumeX,
  Music,
} from 'lucide-react';
import type { PlaybackTick, QueueData, Track, DownloadProgress } from '../api/types';

interface PlayerBarProps {
  playback: PlaybackTick | null;
  queue: QueueData | null;
  downloads?: Record<string, DownloadProgress>;
  isQueueOpen: boolean;
  onToggleQueue: () => void;
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

export const PlayerBar = ({
  playback,
  queue,
  downloads = {},
  isQueueOpen,
  onToggleQueue,
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
  const isPlaying = playback?.state === 'playing';
  const duration = playback?.duration || currentTrack?.duration_s || 0;

  // Local 60fps interpolated position between 250ms server ticks
  const [displayPos, setDisplayPos] = useState<number>(0);
  const [isScrubbing, setIsScrubbing] = useState<boolean>(false);
  const [scrubPos, setScrubPos] = useState<number>(0);

  const lastServerPosRef = useRef<number>(0);
  const lastTickTimeRef = useRef<number>(performance.now());
  const isPlayingRef = useRef<boolean>(false);
  const durationRef = useRef<number>(0);

  isPlayingRef.current = isPlaying;
  durationRef.current = duration;

  // Sync when a new server tick arrives
  useEffect(() => {
    if (playback?.time_pos !== undefined) {
      lastServerPosRef.current = playback.time_pos;
      lastTickTimeRef.current = performance.now();
      if (!isScrubbing) {
        setDisplayPos(playback.time_pos);
      }
    }
  }, [playback?.time_pos, isScrubbing]);

  // RequestAnimationFrame 60fps interpolation loop
  useEffect(() => {
    let animId: number;
    const updateInterpolation = () => {
      if (isPlayingRef.current && !isScrubbing) {
        const elapsed = (performance.now() - lastTickTimeRef.current) / 1000;
        const currentEstimated = Math.min(
          durationRef.current > 0 ? durationRef.current : Infinity,
          lastServerPosRef.current + elapsed
        );
        setDisplayPos(currentEstimated);
      }
      animId = requestAnimationFrame(updateInterpolation);
    };
    animId = requestAnimationFrame(updateInterpolation);
    return () => cancelAnimationFrame(animId);
  }, [isScrubbing]);

  const progressBarRef = useRef<HTMLDivElement>(null);

  const handleSeekFromPointer = useCallback(
    (e: React.PointerEvent | MouseEvent) => {
      if (!progressBarRef.current || duration <= 0) return;
      const rect = progressBarRef.current.getBoundingClientRect();
      const clickX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
      const percentage = clickX / rect.width;
      const targetSec = percentage * duration;
      setScrubPos(targetSec);
      setDisplayPos(targetSec);
    },
    [duration]
  );

  const onPointerDown = (e: React.PointerEvent) => {
    setIsScrubbing(true);
    handleSeekFromPointer(e);
    const onPointerMove = (moveEvent: MouseEvent) => {
      handleSeekFromPointer(moveEvent);
    };
    const onPointerUp = (upEvent: MouseEvent) => {
      if (progressBarRef.current && duration > 0) {
        const rect = progressBarRef.current.getBoundingClientRect();
        const clickX = Math.max(0, Math.min(rect.width, upEvent.clientX - rect.left));
        const finalSec = (clickX / rect.width) * duration;
        onSeek(finalSec);
        setDisplayPos(finalSec);
      }
      setIsScrubbing(false);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
    };
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
  };

  const formatTime = (secs?: number): string => {
    if (!secs || isNaN(secs) || secs < 0) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const currentPos = isScrubbing ? scrubPos : displayPos;
  const progressPct = duration > 0 ? Math.min(100, Math.max(0, (currentPos / duration) * 100)) : 0;

  // Volume handling
  const [volume, setVolume] = useState<number>(playback?.volume ?? 80);
  const [prevVolume, setPrevVolume] = useState<number>(80);

  useEffect(() => {
    if (playback?.volume !== undefined) {
      setVolume(playback.volume);
    }
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

  const isDownloaded =
    currentTrack &&
    (currentTrack.download_status === 'completed' ||
      downloads[currentTrack.video_id]?.status === 'completed' ||
      !!currentTrack.local_path);

  const queueCount = queue?.items?.length || 0;

  // Disciplined 300x300 artwork
  const formatCover = (url?: string | null): string | undefined => {
    if (!url) return undefined;
    if (url.includes('googleusercontent.com') || url.includes('ggpht.com')) {
      return url.replace(/=w\d+-h\d+/, '=w300-h300');
    }
    return url;
  };

  return (
    <footer className="h-[76px] flex-none bg-bg-player border-t border-border-subtle px-4 flex items-center justify-between z-50 select-none">
      {/* Left: Track Info & Offline Indicator */}
      <div className="flex items-center gap-3 w-64 min-w-0">
        <div className="w-12 h-12 rounded bg-bg-surface flex-none overflow-hidden border border-border-subtle flex items-center justify-center">
          {currentTrack?.thumbnail_url ? (
            <img
              src={formatCover(currentTrack.thumbnail_url)}
              alt=""
              loading="lazy"
              decoding="async"
              className="w-full h-full object-cover"
            />
          ) : (
            <Music size={20} className="text-text-2 opacity-50" />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold text-text-1 truncate">
            {currentTrack?.title || 'Ready'}
          </div>
          <div className="text-[11px] text-text-2 truncate flex items-center gap-1.5">
            <span>{currentTrack?.artist || 'MetroLite Desktop'}</span>
            {isDownloaded && (
              <span className="text-[9px] uppercase font-bold text-downloaded bg-downloaded/10 px-1 py-0.2 rounded border border-downloaded/20">
                Offline
              </span>
            )}
          </div>
        </div>

        {currentTrack && (
          <button
            onClick={() => onToggleLike(currentTrack)}
            title={currentTrack.is_liked ? 'Unlike' : 'Like'}
            className="p-1.5 text-text-2 hover:text-liked transition-colors flex-none"
          >
            <Heart
              size={16}
              className={currentTrack.is_liked ? 'fill-liked text-liked' : 'text-text-2'}
            />
          </button>
        )}
      </div>

      {/* Center: Controls + 60fps Scrubber */}
      <div className="flex flex-col items-center gap-1.5 flex-1 max-w-xl px-4">
        {/* Buttons */}
        <div className="flex items-center gap-4">
          <button
            onClick={onToggleShuffle}
            title={queue?.shuffle ? 'Shuffle On' : 'Shuffle Off'}
            className={`p-1.5 rounded transition-colors ${
              queue?.shuffle ? 'text-accent' : 'text-text-2 hover:text-text-1'
            }`}
          >
            <Shuffle size={14} />
          </button>

          <button
            onClick={onPrev}
            title="Previous Track"
            className="p-1.5 text-text-2 hover:text-text-1 transition-colors"
          >
            <SkipBack size={17} />
          </button>

          {/* 40px Solid Circle Play/Pause Button */}
          <button
            onClick={onTogglePlay}
            title={isPlaying ? 'Pause' : 'Play'}
            className="w-10 h-10 rounded-full bg-text-1 text-bg-base flex items-center justify-center hover:scale-105 active:scale-95 transition-transform"
          >
            {isPlaying ? (
              <Pause size={18} className="fill-current" />
            ) : (
              <Play size={18} className="fill-current ml-0.5" />
            )}
          </button>

          <button
            onClick={onNext}
            title="Next Track"
            className="p-1.5 text-text-2 hover:text-text-1 transition-colors"
          >
            <SkipForward size={17} />
          </button>

          <button
            onClick={onCycleRepeat}
            title={`Repeat: ${queue?.repeat_mode || 'off'}`}
            className={`p-1.5 rounded transition-colors ${
              queue?.repeat_mode && queue.repeat_mode !== 'off'
                ? 'text-accent'
                : 'text-text-2 hover:text-text-1'
            }`}
          >
            {queue?.repeat_mode === 'one' ? <Repeat1 size={15} /> : <Repeat size={14} />}
          </button>
        </div>

        {/* 60fps Scrubber Progress Bar */}
        <div className="w-full flex items-center gap-2.5 text-[11px] font-mono text-text-2">
          <span className="w-9 text-right flex-none">{formatTime(currentPos)}</span>

          <div
            ref={progressBarRef}
            onPointerDown={onPointerDown}
            className="flex-1 h-3 flex items-center cursor-pointer group py-1"
          >
            <div className="w-full h-1 bg-white/[0.08] rounded-full overflow-hidden relative group-hover:h-1.5 transition-all">
              <div
                className="h-full bg-accent rounded-full transition-[width] duration-75"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>

          <span className="w-9 text-left flex-none">{formatTime(duration)}</span>
        </div>
      </div>

      {/* Right: Actions, Volume & Queue Drawer Toggle */}
      <div className="flex items-center gap-3 w-64 justify-end">
        {currentTrack && (
          <button
            onClick={() => onDownload(currentTrack)}
            title={isDownloaded ? 'Downloaded' : 'Download track'}
            className="p-1.5 text-text-2 hover:text-downloaded transition-colors"
          >
            {isDownloaded ? (
              <CheckCircle2 size={16} className="text-downloaded" />
            ) : (
              <Download size={16} />
            )}
          </button>
        )}

        {/* Volume Mute & Slider */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={toggleMute}
            title={volume === 0 ? 'Unmute' : 'Mute'}
            className="p-1 text-text-2 hover:text-text-1 transition-colors"
          >
            {volume === 0 ? <VolumeX size={16} /> : <Volume2 size={16} />}
          </button>
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            onChange={(e) => handleVolumeChange(Number(e.target.value))}
            className="w-18 h-1 accent-accent bg-white/[0.12] rounded-lg cursor-pointer"
          />
        </div>

        {/* Right Queue Drawer Toggle Button */}
        <button
          onClick={onToggleQueue}
          title="Toggle Queue Drawer"
          className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs transition-colors border ${
            isQueueOpen
              ? 'bg-bg-hover text-accent border-accent/40 font-semibold'
              : 'text-text-2 hover:text-text-1 hover:bg-bg-hover border-border-subtle'
          }`}
        >
          <ListMusic size={15} />
          {queueCount > 0 && (
            <span className="text-[10px] font-mono ml-0.5">{queueCount}</span>
          )}
        </button>
      </div>
    </footer>
  );
};
