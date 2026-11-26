import { type RefObject, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Download,
  ListPlus,
  Radio,
  PlusCircle,
  Trash2,
  UserRound,
  Disc3,
} from 'lucide-react';
import { getTrackArtists } from '../utils/artists';
import type { DownloadProgress, Track } from '../api/types';

export interface TrackMenuState {
  track: Track;
  top: number;
  left: number;
  isDownloaded: boolean;
}

/**
 * Determine whether a track currently has a completed offline download.
 */
export function isTrackDownloaded(
  track: Track,
  downloadedIds?: Set<string>,
  downloads?: Record<string, DownloadProgress>
): boolean {
  return (
    !!downloadedIds?.has(track.video_id) ||
    track.download_status === 'completed' ||
    downloads?.[track.video_id]?.status === 'completed' ||
    !!track.local_path
  );
}

/**
 * Manage floating track context menu state, clamping inside the viewport and closing on scroll, resize, or Escape.
 */
export function useTrackContextMenu() {
  const [menuState, setMenuState] = useState<TrackMenuState | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuState) return;
    const close = () => setMenuState(null);
    const onScroll = (e: Event) => {
      if (menuRef.current && e.target instanceof Node && menuRef.current.contains(e.target)) {
        return;
      }
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
    window.addEventListener('resize', close);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', onScroll, { capture: true });
      window.removeEventListener('resize', close);
      window.removeEventListener('keydown', onKey);
    };
  }, [menuState]);

  const openMenuAt = useCallback(
    (track: Track, x: number, y: number, isDownloaded: boolean) => {
      const artistCount = getTrackArtists(track).length;
      const menuWidth = 220;
      const extraArtistHeight = artistCount > 1 ? 48 : 0;
      const menuHeight = (isDownloaded ? 204 : 172) + extraArtistHeight;
      const left = Math.max(8, Math.min(x, window.innerWidth - menuWidth - 8));
      const openUpward = y + menuHeight > window.innerHeight - 96;
      const top = openUpward ? Math.max(8, y - menuHeight - 4) : y + 4;
      setMenuState({ track, top, left, isDownloaded });
    },
    []
  );

  const closeMenu = useCallback(() => setMenuState(null), []);

  return { menuState, menuRef, openMenuAt, closeMenu };
}

interface TrackContextMenuProps {
  menuState: TrackMenuState | null;
  menuRef?: RefObject<HTMLDivElement>;
  onClose: () => void;
  onAddNext: (track: Track) => void;
  onAppendQueue?: (track: Track) => void;
  onOpenArtist?: (artistName: string, artistId?: string | null) => void;
  onOpenAlbum?: (albumName: string, albumId: string | null | undefined, track: Track) => void;
  onOpenAddToPlaylist?: (track: Track) => void;
  onDownload?: (track: Track) => void;
  onDeleteDownload?: (track: Track) => void;
}

/**
 * Shared floating track action menu rendered into document.body via React portal.
 */
export function TrackContextMenu({
  menuState,
  menuRef,
  onClose,
  onAddNext,
  onAppendQueue,
  onOpenArtist,
  onOpenAlbum,
  onOpenAddToPlaylist,
  onDownload,
  onDeleteDownload,
}: TrackContextMenuProps) {
  if (!menuState) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[9998]"
      onClick={onClose}
      onContextMenu={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div
        ref={menuRef}
        onClick={(e) => e.stopPropagation()}
        style={{ top: `${menuState.top}px`, left: `${menuState.left}px` }}
        className="fixed w-[220px] ios-liquid-popover eh-popover-in rounded-2xl z-[9999] py-1.5 text-xs select-none"
      >
        <button
          onClick={() => {
            onAddNext(menuState.track);
            onClose();
          }}
          className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
        >
          <Radio size={14} style={{ color: 'var(--dynamic-accent)' }} />
          <span>Play Next</span>
        </button>
        {onAppendQueue && (
          <button
            onClick={() => {
              onAppendQueue(menuState.track);
              onClose();
            }}
            className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
          >
            <ListPlus size={14} className="text-[#A6B0CF]" />
            <span>Add to Queue</span>
          </button>
        )}
        {onOpenArtist && (() => {
          const menuArtists = getTrackArtists(menuState.track);
          if (menuArtists.length <= 1) {
            const single = menuArtists[0];
            return (
              <button
                onClick={() => {
                  onOpenArtist(
                    single?.name || menuState.track.artist,
                    single?.channelId ?? menuState.track.artist_id
                  );
                  onClose();
                }}
                className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
              >
                <UserRound size={14} className="text-[#A6B0CF]" />
                <span className="truncate">
                  {single ? `Go to ${single.name}` : 'Go to Artist'}
                </span>
              </button>
            );
          }
          return (
            <div className="border-t border-white/10 mt-1 pt-1.5 pb-1">
              <div className="px-3.5 py-1 flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.14em] text-[#8E99BA]">
                <UserRound size={12} className="text-[#8E99BA]" />
                <span>Go to Artist</span>
              </div>
              <div className="px-2.5 py-1 flex flex-wrap gap-1.5">
                {menuArtists.map((a, idx) => (
                  <button
                    key={`${a.name}-${idx}`}
                    type="button"
                    onClick={() => {
                      onOpenArtist(a.name, a.channelId);
                      onClose();
                    }}
                    title={`Go to ${a.name}`}
                    className="max-w-full truncate px-2.5 py-1 rounded-full bg-white/[0.06] hover:bg-white/[0.16] border border-white/[0.10] text-[11px] text-[#F4F6FF] hover:text-white transition-colors"
                  >
                    {a.name}
                  </button>
                ))}
              </div>
            </div>
          );
        })()}
        {onOpenAlbum && (
          <button
            onClick={() => {
              onOpenAlbum(
                menuState.track.album || menuState.track.title,
                menuState.track.album_id,
                menuState.track
              );
              onClose();
            }}
            className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
          >
            <Disc3 size={14} className="text-[#A6B0CF]" />
            <span>Go to Album</span>
          </button>
        )}
        {onOpenAddToPlaylist && (
          <button
            onClick={() => {
              onOpenAddToPlaylist(menuState.track);
              onClose();
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
              onClose();
            }}
            className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#FF6B9E] hover:bg-[#FF4D8D]/15 text-left transition-colors border-t border-white/10 mt-1 pt-2"
          >
            <Trash2 size={14} />
            <span>Delete Offline Download</span>
          </button>
        ) : onDownload ? (
          <button
            onClick={() => {
              onDownload(menuState.track);
              onClose();
            }}
            className="w-full flex items-center gap-2.5 px-3.5 py-2 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
          >
            <Download size={14} className="text-[#34D399]" />
            <span>Download Offline</span>
          </button>
        ) : null}
      </div>
    </div>,
    document.body
  );
}
