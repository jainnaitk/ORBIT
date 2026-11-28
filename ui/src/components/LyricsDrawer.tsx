import { useEffect, useState, useRef } from 'react';
import {
  X,
  AlignLeft,
  Loader2,
  Heart,
  Download,
  CheckCircle2,
  Maximize2,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import { ArtistLinks } from './ArtistLinks';
import { isTrackDownloaded } from './TrackContextMenu';
import {
  LyricsOverflowMenu,
  LyricsStageBody,
  LyricsSyncModeTabs,
  useLyricsController,
} from './LyricsStageView';
import { useTwoFingerClose } from '../utils/gestures';
import type { PlaybackTick, QueueData, Track, DownloadProgress } from '../api/types';

interface LyricsDrawerProps {
  isOpen: boolean;
  playback: PlaybackTick | null;
  queue: QueueData | null;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  lyricsEdgeBlur: boolean;
  onClose: () => void;
  onExpandFullscreen: () => void;
  onOpenAlbum?: (track: Track) => void;
  onOpenArtist?: (track: Track, artistName?: string, artistId?: string | null) => void;
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
  lyricsEdgeBlur,
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
  const asideRef = useRef<HTMLElement>(null);

  const lyrics = useLyricsController({
    isActive: isOpen,
    playback,
    queue,
    onSeek,
  });

  useTwoFingerClose({
    enabled: isOpen && rendered,
    ready: visible && !lyrics.loading,
    settleMs: 420,
    containerRef: asideRef,
    onClose,
  });

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

  if (!rendered) return null;

  const currentTrack = lyrics.currentTrack;
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
  const isDownloaded = !!currentTrack && isTrackDownloaded(currentTrack, downloadedIds, downloads);

  return (
    <aside
      ref={asideRef}
      data-testid="lyrics-drawer"
      style={{
        transform: visible ? 'translate3d(0, 0, 0)' : 'translate3d(24px, 0, 0)',
        opacity: visible ? 1 : 0,
        transition: 'transform 220ms cubic-bezier(0.22, 1, 0.36, 1), opacity 180ms ease',
      }}
      className="orbit-drawer z-30 ios-liquid-drawer rounded-[24px] flex flex-col select-none overflow-hidden"
    >
      {/* Header */}
      <div className="h-12 flex-none px-4 flex items-center justify-between gap-2 border-b border-white/[0.10] bg-white/[0.025]">
        <div className="flex items-center gap-2 min-w-0">
          <AlignLeft size={14} style={{ color: 'var(--dynamic-accent)' }} className="flex-none" />
          <span className="text-[11px] font-bold uppercase tracking-[0.15em] text-[#F4F6FF] flex-none">
            Lyrics
          </span>
          <LyricsSyncModeTabs controller={lyrics} compact />
        </div>

        <div className="flex items-center gap-1 flex-none">
          <LyricsOverflowMenu controller={lyrics} />
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

      {/* Compact Track Ribbon */}
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
              <ArtistLinks
                artist={currentTrack.artist}
                artistId={currentTrack.artist_id}
                artists={currentTrack.artists}
                onOpenArtist={
                  onOpenArtist
                    ? (name, id) => onOpenArtist(currentTrack, name, id)
                    : undefined
                }
                className="text-[10px] text-[#9AA5C4]"
              />
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

      {/* Next-Gen Synced Lyrics Stage Canvas */}
      <LyricsStageBody
        variant="drawer"
        controller={lyrics}
        lyricsEdgeBlur={lyricsEdgeBlur}
      />
    </aside>
  );
};
