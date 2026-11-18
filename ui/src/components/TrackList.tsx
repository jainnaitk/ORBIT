import { useRef, useState } from 'react';
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
} from 'lucide-react';
import type { Track, DownloadProgress } from '../api/types';

interface TrackListProps {
  tracks: Track[];
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  onPlay: (track: Track, index: number) => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist?: (track: Track) => void;
  emptyMessage?: string;
}

export const TrackList = ({
  tracks,
  currentTrackId,
  isPlaying,
  downloads = {},
  onPlay,
  onToggleLike,
  onDownload,
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
  emptyMessage = 'No tracks found.',
}: TrackListProps) => {
  const parentRef = useRef<HTMLDivElement>(null);
  const [activeMenuTrackId, setActiveMenuTrackId] = useState<string | null>(null);

  const rowVirtualizer = useVirtualizer({
    count: tracks.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 56, // Fixed 56px row height per UI_SPEC.md
    overscan: 6,
  });

  const formatDuration = (secs?: number): string => {
    if (!secs || isNaN(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  // Convert thumbnail URL to w120-h120 size discipline
  const formatThumb = (url?: string | null): string | undefined => {
    if (!url) return undefined;
    if (url.includes('googleusercontent.com') || url.includes('ggpht.com')) {
      return url.replace(/=w\d+-h\d+/, '=w120-h120');
    }
    return url;
  };

  if (tracks.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-text-2">
        <Music size={32} className="mb-2 opacity-40 text-text-2" />
        <p className="text-xs">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div
      ref={parentRef}
      className="flex-1 overflow-y-auto w-full select-none"
      style={{ contain: 'strict' }}
      onClick={() => setActiveMenuTrackId(null)}
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
          const downloadProgress = downloads[track.video_id];
          const isDownloaded =
            track.download_status === 'completed' ||
            downloadProgress?.status === 'completed' ||
            !!track.local_path;
          const isDownloading =
            downloadProgress?.status === 'downloading' ||
            downloadProgress?.status === 'queued';
          const isMenuOpen = activeMenuTrackId === track.video_id;

          return (
            <div
              key={track.video_id || virtualRow.key}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
              }}
              className={`flex items-center px-4 gap-3.5 border-b border-white/[0.03] hover:bg-bg-hover transition-colors group cursor-pointer ${
                isCurrent ? 'bg-accent/[0.08] text-accent' : 'text-text-1'
              }`}
              onClick={() => onPlay(track, virtualRow.index)}
            >
              {/* Position index / Playing visualizer / Hover Play button */}
              <div className="w-6 flex-none flex items-center justify-center text-xs text-text-2">
                {isCurrent && isPlaying ? (
                  <div className="flex items-end gap-0.5 h-3.5">
                    <span className="w-1 bg-accent rounded-sm h-full animate-bounce"></span>
                    <span
                      className="w-1 bg-accent rounded-sm h-2/3 animate-bounce"
                      style={{ animationDelay: '150ms' }}
                    ></span>
                    <span
                      className="w-1 bg-accent rounded-sm h-5/6 animate-bounce"
                      style={{ animationDelay: '300ms' }}
                    ></span>
                  </div>
                ) : (
                  <>
                    <span className="group-hover:hidden">{virtualRow.index + 1}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onPlay(track, virtualRow.index);
                      }}
                      className="hidden group-hover:flex items-center justify-center text-text-1 hover:text-accent transition-colors"
                    >
                      <Play size={13} className="fill-current" />
                    </button>
                  </>
                )}
              </div>

              {/* 40x40 Thumbnail with disciplined resolution */}
              <div className="w-10 h-10 flex-none rounded bg-bg-surface border border-border-subtle overflow-hidden flex items-center justify-center">
                {track.thumbnail_url ? (
                  <img
                    src={formatThumb(track.thumbnail_url)}
                    alt=""
                    loading="lazy"
                    decoding="async"
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <Music size={16} className="text-text-2" />
                )}
              </div>

              {/* Title & Artist */}
              <div className="flex-1 min-w-0 pr-2">
                <div
                  className={`text-xs font-medium truncate ${
                    isCurrent ? 'text-accent font-semibold' : 'text-text-1'
                  }`}
                >
                  {track.title}
                </div>
                <div className="text-[11px] text-text-2 truncate">{track.artist}</div>
              </div>

              {/* Album (hidden on narrow screens) */}
              <div className="hidden md:block w-44 text-xs text-text-2 truncate pr-2">
                {track.album || '—'}
              </div>

              {/* Like Button */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleLike(track);
                }}
                title={track.is_liked ? 'Unlike' : 'Like'}
                className="p-1.5 text-text-2 hover:text-liked transition-colors flex-none"
              >
                <Heart
                  size={15}
                  className={
                    track.is_liked
                      ? 'fill-liked text-liked'
                      : 'text-text-2 opacity-50 group-hover:opacity-100'
                  }
                />
              </button>

              {/* Download Status Button */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (!isDownloaded && !isDownloading) {
                    onDownload(track);
                  }
                }}
                title={
                  isDownloaded
                    ? 'Downloaded offline'
                    : isDownloading
                    ? `Downloading ${Math.round(downloadProgress?.progress_pct || 0)}%`
                    : 'Download track'
                }
                className="p-1.5 text-text-2 hover:text-downloaded transition-colors flex-none"
              >
                {isDownloaded ? (
                  <CheckCircle2 size={15} className="text-downloaded fill-downloaded/20" />
                ) : isDownloading ? (
                  <Loader2 size={15} className="text-accent animate-spin" />
                ) : (
                  <Download
                    size={15}
                    className="opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity"
                  />
                )}
              </button>

              {/* Monospace Duration */}
              <div className="w-12 text-right font-mono text-xs text-text-2 flex-none">
                {formatDuration(track.duration_s)}
              </div>

              {/* Context Action Menu Dropdown */}
              <div className="relative flex-none">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setActiveMenuTrackId(isMenuOpen ? null : track.video_id);
                  }}
                  title="More actions"
                  className="p-1.5 text-text-2 hover:text-text-1 rounded hover:bg-bg-surface transition-colors"
                >
                  <MoreVertical size={14} />
                </button>

                {isMenuOpen && (
                  <div
                    onClick={(e) => e.stopPropagation()}
                    className="absolute right-0 top-8 w-44 bg-bg-surface border border-border-subtle rounded-md shadow-xl z-30 py-1 text-xs select-none"
                  >
                    <button
                      onClick={() => {
                        onAddNext(track);
                        setActiveMenuTrackId(null);
                      }}
                      className="w-full flex items-center gap-2.5 px-3 py-2 text-text-1 hover:bg-bg-hover text-left transition-colors"
                    >
                      <Radio size={14} className="text-accent" />
                      <span>Play Next</span>
                    </button>
                    <button
                      onClick={() => {
                        onAppendQueue(track);
                        setActiveMenuTrackId(null);
                      }}
                      className="w-full flex items-center gap-2.5 px-3 py-2 text-text-1 hover:bg-bg-hover text-left transition-colors"
                    >
                      <ListPlus size={14} className="text-text-2" />
                      <span>Add to Queue</span>
                    </button>
                    {onOpenAddToPlaylist && (
                      <button
                        onClick={() => {
                          onOpenAddToPlaylist(track);
                          setActiveMenuTrackId(null);
                        }}
                        className="w-full flex items-center gap-2.5 px-3 py-2 text-text-1 hover:bg-bg-hover text-left transition-colors border-t border-border-subtle"
                      >
                        <PlusCircle size={14} className="text-text-2" />
                        <span>Add to Playlist...</span>
                      </button>
                    )}
                    <button
                      onClick={() => {
                        onDownload(track);
                        setActiveMenuTrackId(null);
                      }}
                      className="w-full flex items-center gap-2.5 px-3 py-2 text-text-1 hover:bg-bg-hover text-left transition-colors"
                    >
                      <Download size={14} className="text-downloaded" />
                      <span>{isDownloaded ? 'Re-download' : 'Download Audio'}</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
