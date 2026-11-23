import { useRef, useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  Play,
  Heart,
  Download,
  CheckCircle2,
  Loader2,
  MoreVertical,
  ListPlus,
  Radio,
  PlusCircle,
  Music,
  Trash2,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import type { Track, DownloadProgress } from '../api/types';

interface TrackListProps {
  tracks: Track[];
  loading?: boolean;
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  showPlayCount?: boolean;
  isDownloadsTab?: boolean;
  onPlay: (track: Track, index: number) => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onDeleteDownload?: (track: Track) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist?: (track: Track) => void;
  emptyMessage?: string;
}

interface MenuPosition {
  track: Track;
  top: number;
  left: number;
  isDownloaded: boolean;
}

export const TrackList = ({
  tracks,
  loading = false,
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
  emptyMessage = 'No tracks found.',
}: TrackListProps) => {
  const parentRef = useRef<HTMLDivElement>(null);
  const [menuState, setMenuState] = useState<MenuPosition | null>(null);

  const rowVirtualizer = useVirtualizer({
    count: tracks.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 58,
    overscan: 3,
  });

  useEffect(() => {
    if (!menuState) return;
    const close = () => setMenuState(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    const scrollEl = parentRef.current;
    scrollEl?.addEventListener('scroll', close, { passive: true });
    window.addEventListener('resize', close);
    window.addEventListener('keydown', onKey);
    return () => {
      scrollEl?.removeEventListener('scroll', close);
      window.removeEventListener('resize', close);
      window.removeEventListener('keydown', onKey);
    };
  }, [menuState]);

  const formatDuration = (secs?: number): string => {
    if (!secs || isNaN(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const openMenuAt = (track: Track, x: number, y: number, isDownloaded: boolean) => {
    const menuWidth = 204;
    const menuHeight = isDownloaded ? 204 : 172;
    const left = Math.max(8, Math.min(x, window.innerWidth - menuWidth - 8));
    const openUpward = y + menuHeight > window.innerHeight - 96;
    const top = openUpward ? Math.max(8, y - menuHeight - 4) : y + 4;
    setMenuState({ track, top, left, isDownloaded });
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
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-[#A6B0CF] bg-transparent">
        <Music size={32} className="mb-2 opacity-35" />
        <p className="text-xs">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <>
      <div className="flex-none flex items-center px-3 sm:px-5 py-2 gap-2.5 sm:gap-3.5 border-b border-white/[0.08] bg-white/[0.02] text-[10px] font-semibold uppercase tracking-[0.16em] text-[#7A84A6] select-none">
        <div className="hidden sm:block w-6 flex-none text-center">#</div>
        <div className="flex-1 min-w-0">Title</div>
        {showPlayCount && <div className="hidden sm:block flex-none w-16 text-right">Plays</div>}
        <div className="hidden md:block w-40 flex-none pr-2">Album</div>
        <div className="w-[34px] sm:w-[62px] flex-none" />
        <div className="hidden sm:block w-12 text-right flex-none">Time</div>
        <div className="w-7 flex-none" />
      </div>

      <div
        ref={parentRef}
        className="flex-1 overflow-y-auto w-full select-none bg-transparent"
        style={{ contain: 'strict' }}
      >
        <div
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
            const isDownloaded =
              downloadedIds?.has(track.video_id) ||
              track.download_status === 'completed' ||
              downloadProgress?.status === 'completed' ||
              !!track.local_path;
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
                  transform: `translateY(${virtualRow.start}px)`,
                }}
                className={`flex items-center px-3 sm:px-5 gap-2.5 sm:gap-3.5 border-b border-white/[0.045] hover:bg-white/[0.06] transition-colors group cursor-pointer ${
                  isCurrent ? 'bg-white/[0.08]' : ''
                }`}
                onClick={() => onPlay(track, virtualRow.index)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  openMenuAt(track, e.clientX, e.clientY, isDownloaded);
                }}
              >
                {isCurrent && (
                  <span
                    className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r-full"
                    style={{ background: 'var(--dynamic-accent)' }}
                  />
                )}
                <div className="hidden sm:flex w-6 flex-none items-center justify-center text-xs text-[#646E8F]">
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

                <div className="w-9 h-9 sm:w-10 sm:h-10 flex-none rounded-xl bg-black/70 border border-white/15 overflow-hidden">
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
                  <div className="text-[11px] text-[#A6B0CF] truncate">{track.artist}</div>
                </div>

                {showPlayCount && (track.play_count ?? 0) > 0 && (
                  <div className="hidden sm:block flex-none text-[10px] font-mono tabular-nums text-[#A6B0CF] bg-white/[0.08] border border-white/12 px-2 py-0.5 rounded-full">
                    {track.play_count} {track.play_count === 1 ? 'play' : 'plays'}
                  </div>
                )}

                <div className="hidden md:block w-40 text-xs text-[#A6B0CF] truncate pr-2">
                  {track.album || '—'}
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

                {isDownloadsTab && isDownloaded && onDeleteDownload ? (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteDownload(track);
                    }}
                    title="Delete offline download"
                    className="hidden sm:inline-flex p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] hover:bg-[#FF4D8D]/10 rounded-full transition-colors flex-none"
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
                    className="hidden sm:inline-flex p-1.5 text-[#A6B0CF] hover:text-[#34D399] transition-colors flex-none"
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
                )}

                <div className="hidden sm:block w-12 text-right font-mono tabular-nums text-xs text-[#A6B0CF] flex-none">
                  {formatDuration(track.duration_s)}
                </div>

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (isMenuOpen) return setMenuState(null);
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

      {menuState &&
        createPortal(
          <div
            className="fixed inset-0 z-[9998]"
            onClick={() => setMenuState(null)}
            onContextMenu={(e) => {
              e.preventDefault();
              setMenuState(null);
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{ top: `${menuState.top}px`, left: `${menuState.left}px` }}
              className="fixed w-[204px] ios-liquid-popover eh-popover-in rounded-2xl z-[9999] py-1.5 text-xs select-none"
            >
              <button
                onClick={() => {
                  onAddNext(menuState.track);
                  setMenuState(null);
                }}
                className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
              >
                <Radio size={14} style={{ color: 'var(--dynamic-accent)' }} />
                <span>Play Next</span>
              </button>
              <button
                onClick={() => {
                  onAppendQueue(menuState.track);
                  setMenuState(null);
                }}
                className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
              >
                <ListPlus size={14} className="text-[#A6B0CF]" />
                <span>Add to Queue</span>
              </button>
              {onOpenAddToPlaylist && (
                <button
                  onClick={() => {
                    onOpenAddToPlaylist(menuState.track);
                    setMenuState(null);
                  }}
                  className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors border-t border-white/10 mt-1 pt-2"
                >
                  <PlusCircle size={14} className="text-[#A6B0CF]" />
                  <span>Add to Playlist...</span>
                </button>
              )}

              {menuState.isDownloaded && onDeleteDownload ? (
                <button
                  onClick={() => {
                    onDeleteDownload(menuState.track);
                    setMenuState(null);
                  }}
                  className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#FF6B9E] hover:bg-[#FF4D8D]/15 text-left transition-colors border-t border-white/10 mt-1 pt-2"
                >
                  <Trash2 size={14} />
                  <span>Delete Offline Download</span>
                </button>
              ) : (
                <button
                  onClick={() => {
                    onDownload(menuState.track);
                    setMenuState(null);
                  }}
                  className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
                >
                  <Download size={14} className="text-[#34D399]" />
                  <span>Download Offline</span>
                </button>
              )}
            </div>
          </div>,
          document.body
        )}
    </>
  );
};
