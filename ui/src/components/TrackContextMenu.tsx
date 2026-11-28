import {
  createContext,
  type ReactNode,
  type RefObject,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import {
  Disc3,
  Download,
  ListPlus,
  ListStart,
  Pin,
  PinOff,
  PlusCircle,
  Radio,
  Share2,
  Trash2,
  UserRound,
} from 'lucide-react';
import { getTrackArtists } from '../utils/artists';
import type { DownloadProgress, Track } from '../api/types';

export interface TrackMenuState {
  track: Track;
  top: number;
  left: number;
  isDownloaded: boolean;
}

export interface TrackActionsContextValue {
  pinnedTrackIds?: Set<string>;
  followedArtistKeys?: Set<string>;
  isArtistFollowed?: (name?: string | null, channelId?: string | null) => boolean;
  onToggleFollowArtist?: (artist: {
    name: string;
    channelId?: string | null;
    thumbnailUrl?: string | null;
  }) => Promise<boolean>;
  onPinToggle?: (track: Track) => void;
  onStartRadio?: (track: Track) => void;
  onShareTrack?: (track: Track) => void;
  onOpenAddToPlaylist?: (track: Track) => void;
}

const TrackActionsContext = createContext<TrackActionsContextValue>({});

export function TrackActionsProvider({
  value,
  children,
}: {
  value: TrackActionsContextValue;
  children: ReactNode;
}) {
  return (
    <TrackActionsContext.Provider value={value}>
      {children}
    </TrackActionsContext.Provider>
  );
}

export function useTrackActions(): TrackActionsContextValue {
  return useContext(TrackActionsContext);
}

/**
 * Determine whether a track currently has a completed offline download.
 */
export function isTrackDownloaded(
  track: Track,
  downloadedIds?: Set<string>,
  downloads?: Record<string, DownloadProgress>
): boolean {
  const dlStatus = downloads?.[track.video_id]?.status;
  if (dlStatus === 'deleted') return false;
  if (dlStatus === 'completed') return true;
  if (downloadedIds !== undefined) {
    return downloadedIds.has(track.video_id);
  }
  return track.download_status === 'completed' || !!track.local_path;
}

/**
 * Manage floating track context menu state with viewport clamping and deliberate-only dismissal
 * (immune to background lyric/carousel auto-scrolls and two-finger tap synthetic click events).
 */
export function useTrackContextMenu() {
  const [menuState, setMenuState] = useState<TrackMenuState | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const openedAtRef = useRef<number>(0);

  const isOpen = menuState !== null;

  useEffect(() => {
    if (!isOpen) return;
    const close = () => setMenuState(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    window.addEventListener('resize', close);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('resize', close);
      window.removeEventListener('keydown', onKey);
    };
  }, [isOpen]);

  const openMenuAt = useCallback(
    (track: Track, x: number, y: number, isDownloaded: boolean) => {
      openedAtRef.current = performance.now();
      const artistCount = getTrackArtists(track).length;
      const menuWidth = 264;
      const estimatedHeight = Math.min(window.innerHeight - 32, 310 + (artistCount > 1 ? 46 : 0));
      const left = Math.max(8, Math.min(x, window.innerWidth - menuWidth - 8));
      const openUpward = y + estimatedHeight > window.innerHeight - 24;
      const top = openUpward
        ? Math.max(12, y - estimatedHeight - 4)
        : Math.min(y + 4, Math.max(12, window.innerHeight - estimatedHeight - 12));
      setMenuState({ track, top, left, isDownloaded });
    },
    []
  );

  const closeMenu = useCallback(() => setMenuState(null), []);

  const canDismissBackdrop = useCallback(() => {
    return performance.now() - openedAtRef.current > 140;
  }, []);

  return { menuState, menuRef, openMenuAt, closeMenu, canDismissBackdrop };
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
  canDismissBackdrop?: () => boolean;
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
  canDismissBackdrop,
}: TrackContextMenuProps) {
  const actions = useTrackActions();
  const openedAtRef = useRef<number>(performance.now());

  useEffect(() => {
    if (menuState) {
      openedAtRef.current = performance.now();
    }
  }, [menuState]);

  if (!menuState) return null;

  const track = menuState.track;
  const isPinned = Boolean(actions.pinnedTrackIds?.has(track.video_id));
  const menuArtists = getTrackArtists(track);
  const playlistHandler = onOpenAddToPlaylist || actions.onOpenAddToPlaylist;

  const handleBackdropDismiss = (e: React.SyntheticEvent) => {
    if (e.target !== e.currentTarget) return;
    if (performance.now() - openedAtRef.current < 160) return;
    if (canDismissBackdrop && !canDismissBackdrop()) return;
    onClose();
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[9998]"
      onPointerDown={handleBackdropDismiss}
      onContextMenu={(e) => {
        e.preventDefault();
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={menuRef}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        style={{ top: `${menuState.top}px`, left: `${menuState.left}px` }}
        className="fixed w-[264px] max-h-[calc(100vh-24px)] overflow-y-auto no-scrollbar ios-liquid-popover eh-popover-in rounded-2xl z-[9999] py-2 text-xs select-none"
      >
        {/* Top Track Preview Header */}
        <div className="flex items-center gap-2.5 px-3 pb-2 border-b border-white/[0.08]">
          {track.thumbnail_url ? (
            <img
              src={track.thumbnail_url}
              alt={track.title}
              className="w-9 h-9 rounded-lg object-cover bg-white/[0.06] flex-none aspect-square"
            />
          ) : (
            <div className="w-9 h-9 rounded-lg bg-white/[0.06] flex items-center justify-center flex-none">
              <Disc3 size={15} className="text-[#8E99BA]" />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="text-[12.5px] font-semibold text-[#F4F6FF] truncate">
              {track.title}
            </div>
            <div className="text-[11px] text-[#8E99BA] truncate">{track.artist}</div>
          </div>
        </div>

        {/* 3 Quick Action Tiles */}
          <div className="grid grid-cols-3 gap-1.5 px-2.5 py-2 border-b border-white/[0.08]">
            {actions.onStartRadio && (
              <button
                type="button"
                onClick={() => {
                  actions.onStartRadio?.(track);
                  onClose();
                }}
                className="flex flex-col items-center justify-center gap-1 py-2 px-1 rounded-xl bg-white/[0.05] hover:bg-white/[0.12] text-[#F4F6FF] transition-colors"
              >
                <Radio size={14} style={{ color: 'var(--dynamic-accent)' }} />
                <span className="text-[10.5px] font-medium truncate max-w-full">Radio</span>
              </button>
            )}
            {playlistHandler && (
              <button
                type="button"
                onClick={() => {
                  playlistHandler(track);
                  onClose();
                }}
                className="flex flex-col items-center justify-center gap-1 py-2 px-1 rounded-xl bg-white/[0.05] hover:bg-white/[0.12] text-[#F4F6FF] transition-colors"
              >
                <PlusCircle size={14} className="text-[#A6B0CF]" />
                <span className="text-[10.5px] font-medium truncate max-w-full">Playlist</span>
              </button>
            )}
              <button
                type="button"
                onClick={() => {
                  onAddNext(track);
                  onClose();
                }}
                className="flex flex-col items-center justify-center gap-1 py-2 px-1 rounded-xl bg-white/[0.05] hover:bg-white/[0.12] text-[#F4F6FF] transition-colors"
              >
                <ListStart size={14} style={{ color: 'var(--dynamic-accent)' }} />
                <span className="text-[10.5px] font-medium truncate max-w-full">Play Next</span>
              </button>
          </div>

        <div className="py-1">
          {onAppendQueue && (
            <button
              type="button"
              onClick={() => {
                onAppendQueue(track);
                onClose();
              }}
              className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
            >
              <ListPlus size={14} className="text-[#A6B0CF]" />
              <span>Add to Queue</span>
            </button>
          )}

          {actions.onPinToggle && (
            <button
              type="button"
              onClick={() => {
                actions.onPinToggle?.(track);
                onClose();
              }}
              className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
            >
              {isPinned ? (
                <>
                  <PinOff size={14} className="text-[#F59E0B]" />
                  <span>Unpin from Speed Dial</span>
                </>
              ) : (
                <>
                  <Pin size={14} className="text-[#A6B0CF]" />
                  <span>Pin to Speed Dial</span>
                </>
              )}
            </button>
          )}

          {menuState.isDownloaded && onDeleteDownload ? (
            <button
              type="button"
              onClick={() => {
                onDeleteDownload(track);
                onClose();
              }}
              className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#FF6B9E] hover:bg-[#FF4D8D]/15 text-left transition-colors"
            >
              <Trash2 size={14} />
              <span>Delete Offline Download</span>
            </button>
          ) : onDownload ? (
            <button
              type="button"
              onClick={() => {
                onDownload(track);
                onClose();
              }}
              className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
            >
              <Download size={14} className="text-[#34D399]" />
              <span>Download Offline</span>
            </button>
          ) : null}
        </div>

        {(onOpenArtist || onOpenAlbum) && (
          <div className="border-t border-white/[0.08] pt-1">
            {onOpenArtist &&
              (menuArtists.length <= 1 ? (
                <button
                  type="button"
                  onClick={() => {
                    const single = menuArtists[0];
                    onOpenArtist(single?.name || track.artist, single?.channelId ?? track.artist_id);
                    onClose();
                  }}
                  className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
                >
                  <UserRound size={14} className="text-[#A6B0CF]" />
                  <span className="truncate">
                    {menuArtists[0] ? `Go to ${menuArtists[0].name}` : 'Go to Artist'}
                  </span>
                </button>
              ) : (
                <div className="py-1">
                  <div className="px-3.5 py-0.5 flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.14em] text-[#8E99BA]">
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
              ))}

            {onOpenAlbum && (
              <button
                type="button"
                onClick={() => {
                  onOpenAlbum(track.album || track.title, track.album_id, track);
                  onClose();
                }}
                className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
              >
                <Disc3 size={14} className="text-[#A6B0CF]" />
                <span>Go to Album</span>
              </button>
            )}
          </div>
        )}
        {actions.onShareTrack && (
          <button type="button" className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#A6B0CF] hover:bg-white/10" onClick={() => { actions.onShareTrack?.(track); onClose(); }}>
            <Share2 size={14} /> Share
          </button>
        )}
      </div>
    </div>,
    document.body
  );
}
