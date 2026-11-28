import { memo, useRef, useEffect } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  Play,
  Heart,
  Download,
  CheckCircle2,
  Loader2,
  MoreVertical,
  Music,
  Trash2,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import { ArtistLinks } from './ArtistLinks';
import { cancelTrackPreload, scheduleTrackPreload } from '../api/client';
import {
  TrackContextMenu,
  isTrackDownloaded,
  useTrackContextMenu,
} from './TrackContextMenu';
import type { Track, DownloadProgress } from '../api/types';

interface TrackListProps {
  tracks: Track[];
  loading?: boolean;
  scrollKey?: string;
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  showPlayCount?: boolean;
  isDownloadsTab?: boolean;
  onPlay: (track: Track, index: number) => void;
  onToggleLike: (track: Track) => void;
  onDownload?: (track: Track) => void;
  onDeleteDownload?: (track: Track) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist?: (track: Track) => void;
  onOpenArtist?: (artistName: string, artistId?: string | null) => void;
  onOpenAlbum?: (albumName: string, albumId: string | null | undefined, track: Track) => void;
  onEmptyAreaClick?: () => void;
  emptyMessage?: string;
}

export const TrackList = memo(({
  tracks,
  loading = false,
  scrollKey,
  currentTrackId,
  isPlaying,
  downloads = {},
  likedIds,
  downloadedIds,
  showPlayCount = false,
  isDownloadsTab = false,
  onPlay,
  onToggleLike,
  onDownload,
  onDeleteDownload,
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
  onOpenArtist,
  onOpenAlbum,
  onEmptyAreaClick,
  emptyMessage = 'No tracks found.',
}: TrackListProps) => {
  const parentRef = useRef<HTMLDivElement>(null);
  const { menuState, menuRef, openMenuAt, closeMenu } = useTrackContextMenu();

  const rowVirtualizer = useVirtualizer({
    count: tracks.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 58,
    overscan: 8,
  });

  useEffect(() => {
    if (scrollKey !== undefined && parentRef.current) {
      parentRef.current.scrollTop = 0;
    }
  }, [scrollKey]);

  const formatDuration = (secs?: number): string => {
    if (!secs || isNaN(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  if (loading && tracks.length === 0) {
    return (
      <div className="flex-1 overflow-hidden px-3 sm:px-5 py-3 space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <div
            key={i}
            className="h-[52px] rounded-xl bg-white/[0.04] border border-white/[0.05] px-3 flex items-center gap-3 eh-skeleton"
            style={{ animationDelay: `${i * 70}ms` }}
          >
            <div className="w-9 h-9 rounded-xl bg-white/[0.08] flex-none" />
            <div className="flex-1 space-y-1.5">
              <div className="h-3 w-44 max-w-[60%] rounded-full bg-white/[0.09]" />
              <div className="h-2.5 w-28 max-w-[40%] rounded-full bg-white/[0.06]" />
            </div>
            <div className="hidden sm:block h-2.5 w-10 rounded-full bg-white/[0.06]" />
          </div>
        ))}
      </div>
    );
  }

  if (tracks.length === 0) {
    return (
      <div
        onClick={() => onEmptyAreaClick?.()}
        className="flex-1 flex flex-col items-center justify-center p-8 text-center text-[#A6B0CF] bg-transparent"
      >
        <Music size={32} className="mb-2 opacity-35" />
        <p className="text-xs">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="track-table flex flex-col flex-1 min-w-0 min-h-0" data-downloads={Boolean(onDownload)}>
      <div
        className="track-columns track-heading flex-none py-2 border-b border-white/[0.08] bg-white/[0.02] text-[10px] font-semibold uppercase tracking-[0.16em] text-[#7A84A6] select-none"
      >
        <div className="track-number text-center">#</div><div>Title</div>
        <div className="track-album">Album</div><div />{onDownload && <div className="track-download" />}
        <div className="track-time text-right">Time</div><div />
      </div>

      <div
        ref={parentRef}
        onClick={(e) => {
          if (e.target === e.currentTarget) onEmptyAreaClick?.();
        }}
        className="flex-1 overflow-y-auto w-full select-none bg-transparent eh-scroll-isolate"
        style={{ contain: 'strict', scrollbarGutter: 'stable', willChange: 'scroll-position' }}
      >
        <div
          onClick={(e) => {
            if (e.target === e.currentTarget) onEmptyAreaClick?.();
          }}
          style={{
            height: `${rowVirtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {rowVirtualizer.getVirtualItems().map((virtualRow) => {
            const track = tracks[virtualRow.index];
            const isCurrent = currentTrackId === track.video_id;
            const isLiked = likedIds ? likedIds.has(track.video_id) : !!track.is_liked;
            const downloadProgress = downloads[track.video_id];
            const isDownloaded = isTrackDownloaded(track, downloadedIds, downloads);
            const isDownloading =
              downloadProgress?.status === 'downloading' ||
              downloadProgress?.status === 'queued' ||
              downloadProgress?.status === 'tagging';
            const dlPct = Math.round(
              downloadProgress?.percent ?? downloadProgress?.progress_pct ?? 0
            );
            const isMenuOpen = menuState?.track.video_id === track.video_id;

            return (
              <div
                key={`${track.video_id}-${virtualRow.index}`}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: `${virtualRow.size}px`,
                  transform: `translate3d(0, ${virtualRow.start}px, 0)`,
                  contain: 'layout style paint',
                }}
                className={`track-columns border-b border-white/[0.045] hover:bg-white/[0.06] group cursor-pointer ${
                  isCurrent ? 'bg-white/[0.08]' : ''
                }`}
                onClick={() => onPlay(track, virtualRow.index)}
                onMouseEnter={() => scheduleTrackPreload(track)}
                onMouseLeave={() => cancelTrackPreload(track.video_id)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  openMenuAt(track, e.clientX, e.clientY, isDownloaded);
                }}
              >
                {isCurrent && (
                  <span
                    className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r-full"
                    style={{ background: 'var(--dynamic-accent)' }}
                  />
                )}
                <div className="track-number flex items-center justify-center text-xs text-[#646E8F]">
                  {isCurrent && isPlaying ? (
                    <div className="flex items-end gap-0.5 h-3.5">
                      <span
                        className="w-1 rounded-sm h-full eh-eq-bar"
                        style={{ background: 'var(--dynamic-accent)' }}
                      />
                      <span
                        className="w-1 rounded-sm h-full eh-eq-bar"
                        style={{ background: 'var(--dynamic-accent)', animationDelay: '180ms' }}
                      />
                      <span
                        className="w-1 rounded-sm h-full eh-eq-bar"
                        style={{ background: 'var(--dynamic-accent)', animationDelay: '340ms' }}
                      />
                    </div>
                  ) : (
                    <>
                      <span className="group-hover:hidden tabular-nums">{virtualRow.index + 1}</span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onPlay(track, virtualRow.index);
                        }}
                        className="hidden group-hover:flex items-center justify-center text-white transition-colors"
                      >
                        <Play size={13} className="fill-current" />
                      </button>
                    </>
                  )}
                </div>

                <div className="flex items-center min-w-0 gap-2"><div className="w-9 h-9 flex-none rounded-xl bg-black/70 border border-white/15 overflow-hidden">
                  <TrackImage
                    src={track.thumbnail_url}
                    videoId={track.video_id}
                    size="sm"
                    iconSize={16}
                  />
                </div>

                <div className="flex-1 min-w-0 pr-1 sm:pr-2">
                  <div
                    className="text-xs font-medium truncate"
                    style={
                      isCurrent
                        ? { color: 'var(--dynamic-accent)', fontWeight: 600 }
                        : { color: '#F4F6FF' }
                    }
                  >
                    {track.title}
                  </div>
                  <ArtistLinks
                    artist={track.artist}
                    artistId={track.artist_id}
                    artists={track.artists}
                    onOpenArtist={onOpenArtist}
                    className="text-[11px] text-[#A6B0CF]"
                  />
                </div>

                {showPlayCount && (track.play_count ?? 0) > 0 && (
                  <div className="track-plays flex-none text-[10px] font-mono tabular-nums text-[#A6B0CF] px-1">
                    {track.play_count} {track.play_count === 1 ? 'play' : 'plays'}
                  </div>
                )}

                </div>
                <div className="track-album min-w-0 text-xs text-[#A6B0CF] truncate">
                  {track.album && onOpenAlbum ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenAlbum(track.album!, track.album_id, track);
                      }}
                      className="block max-w-full text-left hover:text-white hover:underline truncate"
                    >
                      {track.album}
                    </button>
                  ) : (
                    track.album || '—'
                  )}
                </div>

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleLike(track);
                  }}
                  title={isLiked ? 'Unlike' : 'Like'}
                  className="p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] transition-colors flex-none"
                >
                  <Heart
                    size={15}
                    className={
                      isLiked
                        ? 'fill-[#FF4D8D] text-[#FF4D8D]'
                        : 'text-[#A6B0CF] opacity-55 group-hover:opacity-100'
                    }
                  />
                </button>

                {onDownload && (isDownloadsTab && isDownloaded && onDeleteDownload ? (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteDownload(track);
                    }}
                    title="Delete offline download"
                     className="track-download p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] rounded-full"
                  >
                    <Trash2 size={14} />
                  </button>
                ) : (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!isDownloaded && !isDownloading) onDownload(track);
                    }}
                    title={
                      isDownloaded
                        ? 'Downloaded offline (+ .lrc)'
                        : isDownloading
                        ? `Downloading ${dlPct}%`
                        : 'Download track & synced lyrics'
                    }
                    className="track-download p-1.5 text-[#A6B0CF] hover:text-[#34D399]"
                  >
                    {isDownloaded ? (
                      <CheckCircle2 size={15} className="text-[#34D399]" />
                    ) : isDownloading ? (
                      <Loader2 size={15} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
                    ) : (
                      <Download
                        size={15}
                        className="opacity-0 group-hover:opacity-70 hover:!opacity-100 transition-opacity"
                      />
                    )}
                  </button>
                ))}

                <div className="track-time text-right font-mono tabular-nums text-xs text-[#A6B0CF]">
                  {formatDuration(track.duration_s)}
                </div>

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (isMenuOpen) return closeMenu();
                    const rect = e.currentTarget.getBoundingClientRect();
                    openMenuAt(track, rect.right - 204, rect.bottom, isDownloaded);
                  }}
                  title="More actions"
                  className={`p-1.5 rounded-full transition-colors flex-none ${
                    isMenuOpen
                      ? 'bg-white/15 text-white'
                      : 'text-[#A6B0CF] hover:text-white hover:bg-white/10'
                  }`}
                >
                  <MoreVertical size={14} />
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <TrackContextMenu
        menuState={menuState}
        menuRef={menuRef}
        onClose={closeMenu}
        onAddNext={onAddNext}
        onAppendQueue={onAppendQueue}
        onOpenArtist={onOpenArtist}
        onOpenAlbum={onOpenAlbum}
        onOpenAddToPlaylist={onOpenAddToPlaylist}
        onDownload={onDownload}
        onDeleteDownload={onDeleteDownload}
      />
    </div>
  );
});
