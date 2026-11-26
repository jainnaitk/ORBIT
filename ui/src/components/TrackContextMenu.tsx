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
  Ban,
  Copy,
  Disc3,
  Download,
  Info,
  ListPlus,
  ListStart,
  Pencil,
  Pin,
  PinOff,
  PlusCircle,
  Radio,
  Share2,
  Trash2,
  UserRound,
  X,
} from 'lucide-react';
import { getTrackArtists } from '../utils/artists';
import { bridgeApi } from '../api/client';
import type { DownloadProgress, Track, TrackDetails } from '../api/types';

export interface TrackMenuState {
  track: Track;
  top: number;
  left: number;
  isDownloaded: boolean;
}

export interface TrackActionsContextValue {
  pinnedTrackIds?: Set<string>;
  onPinToggle?: (track: Track) => void;
  onStartRadio?: (track: Track) => void;
  onShareTrack?: (track: Track) => void;
  onBlockArtist?: (artistName: string, track: Track) => void;
  onSaveTrackMetadata?: (
    track: Track,
    patch: { title: string; artist: string; album: string | null }
  ) => Promise<void>;
  onOpenAddToPlaylist?: (track: Track) => void;
  onOpenDetailsModal?: (track: Track) => void;
  onOpenEditModal?: (track: Track) => void;
}

const TrackActionsContext = createContext<TrackActionsContextValue>({});

export function TrackActionsProvider({
  value,
  children,
}: {
  value: TrackActionsContextValue;
  children: ReactNode;
}) {
  const [detailsTrack, setDetailsTrack] = useState<Track | null>(null);
  const [editTrack, setEditTrack] = useState<Track | null>(null);

  const mergedValue: TrackActionsContextValue = {
    ...value,
    onOpenDetailsModal: (track) => setDetailsTrack(track),
    onOpenEditModal: (track) => setEditTrack(track),
  };

  return (
    <TrackActionsContext.Provider value={mergedValue}>
      {children}
      {detailsTrack && (
        <TrackDetailsModal track={detailsTrack} onClose={() => setDetailsTrack(null)} />
      )}
      {editTrack && value.onSaveTrackMetadata && (
        <EditTrackMetadataModal
          track={editTrack}
          onClose={() => setEditTrack(null)}
          onSave={async (patch) => {
            await value.onSaveTrackMetadata!(editTrack, patch);
            setEditTrack(null);
          }}
        />
      )}
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
      const menuWidth = 264;
      const estimatedHeight = Math.min(window.innerHeight - 32, 410 + (artistCount > 1 ? 46 : 0));
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
 * Shared floating power-user track action menu rendered into document.body via React portal.
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
  const actions = useTrackActions();
  const [confirmBlock, setConfirmBlock] = useState(false);

  useEffect(() => {
    setConfirmBlock(false);
  }, [menuState?.track.video_id]);

  if (!menuState) return null;

  const track = menuState.track;
  const isPinned = Boolean(actions.pinnedTrackIds?.has(track.video_id));
  const menuArtists = getTrackArtists(track);
  const primaryArtist = menuArtists[0]?.name || track.artist;
  const playlistHandler = onOpenAddToPlaylist || actions.onOpenAddToPlaylist;

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
        className="fixed w-[264px] max-h-[calc(100vh-24px)] overflow-y-auto no-scrollbar ios-liquid-popover eh-popover-in rounded-2xl z-[9999] py-2 text-xs select-none"
      >
        {/* Top Track Preview Header */}
        <div className="flex items-center gap-2.5 px-3 pb-2 border-b border-white/[0.08]">
          {track.thumbnail_url ? (
            <img
              src={track.thumbnail_url}
              alt={track.title}
              className="w-9 h-9 rounded-lg object-cover bg-white/[0.06] flex-none"
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
        {(actions.onStartRadio || playlistHandler || actions.onShareTrack) && (
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
            {actions.onShareTrack && (
              <button
                type="button"
                onClick={() => {
                  actions.onShareTrack?.(track);
                  onClose();
                }}
                className="flex flex-col items-center justify-center gap-1 py-2 px-1 rounded-xl bg-white/[0.05] hover:bg-white/[0.12] text-[#F4F6FF] transition-colors"
              >
                <Share2 size={14} className="text-[#A6B0CF]" />
                <span className="text-[10.5px] font-medium truncate max-w-full">Share</span>
              </button>
            )}
          </div>
        )}

        <div className="py-1">
          <button
            type="button"
            onClick={() => {
              onAddNext(track);
              onClose();
            }}
            className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
          >
            <ListStart size={14} style={{ color: 'var(--dynamic-accent)' }} />
            <span>Play Next</span>
          </button>

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
          <div className="border-t border-white/[0.08] py-1">
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

        {(actions.onSaveTrackMetadata || actions.onOpenEditModal || actions.onOpenDetailsModal || actions.onBlockArtist) && (
          <div className="border-t border-white/[0.08] pt-1">
            {actions.onSaveTrackMetadata && actions.onOpenEditModal && (
              <button
                type="button"
                onClick={() => {
                  actions.onOpenEditModal?.(track);
                  onClose();
                }}
                className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
              >
                <Pencil size={14} className="text-[#A6B0CF]" />
                <span>Edit Track Metadata</span>
              </button>
            )}

            {actions.onOpenDetailsModal && (
              <button
                type="button"
                onClick={() => {
                  actions.onOpenDetailsModal?.(track);
                  onClose();
                }}
                className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#F4F6FF] hover:bg-white/12 text-left transition-colors"
              >
                <Info size={14} className="text-[#A6B0CF]" />
                <span>Track Details</span>
              </button>
            )}

            {actions.onBlockArtist && primaryArtist && primaryArtist.toLowerCase() !== 'unknown artist' && (
              confirmBlock ? (
                <button
                  type="button"
                  onClick={() => {
                    actions.onBlockArtist?.(primaryArtist, track);
                    onClose();
                  }}
                  className="w-full flex items-center justify-between gap-2 px-3.5 py-1.5 bg-[#FF4D8D]/20 text-[#FF85B3] font-semibold text-left transition-colors"
                >
                  <span className="truncate">Confirm block {primaryArtist}?</span>
                  <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-[#FF4D8D]/30 text-white">
                    Block
                  </span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmBlock(true)}
                  className="w-full flex items-center gap-2.5 px-3.5 py-1.5 text-[#FF6B9E] hover:bg-[#FF4D8D]/15 text-left transition-colors"
                >
                  <Ban size={14} />
                  <span className="truncate">Block {primaryArtist}</span>
                </button>
              )
            )}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}

function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return '—';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function TrackDetailsModal({ track, onClose }: { track: Track; onClose: () => void }) {
  const [details, setDetails] = useState<TrackDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    bridgeApi
      .getPlayerDetails(track.video_id)
      .then((res) => {
        if (!cancelled) setDetails(res);
      })
      .catch(() => {
        if (!cancelled) {
          setDetails({
            video_id: track.video_id,
            title: track.title,
            artist: track.artist,
            album: track.album,
            duration_s: track.duration_s,
            local_path: track.local_path,
          });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [track]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const rows: { label: string; value: string }[] = [
    { label: 'Video ID', value: details?.video_id || track.video_id },
    { label: 'Title', value: details?.title || track.title },
    { label: 'Artist', value: details?.artist || track.artist },
    { label: 'Album', value: details?.album || track.album || '—' },
    { label: 'Codec', value: details?.codec ? details.codec.toUpperCase() : '—' },
    {
      label: 'Bitrate',
      value: details?.bitrate_kbps ? `${details.bitrate_kbps} kbps` : '—',
    },
    {
      label: 'Sample Rate',
      value: details?.sample_rate_hz
        ? `${details.sample_rate_hz.toLocaleString()} Hz`
        : '—',
    },
    {
      label: 'Channels',
      value: details?.channel_layout
        ? `${details.channel_layout}${details.channel_count ? ` (${details.channel_count})` : ''}`
        : details?.channel_count
        ? `${details.channel_count} ch`
        : '—',
    },
    { label: 'File Size', value: formatBytes(details?.file_size_bytes) },
    {
      label: 'Source Mode',
      value: details?.is_offline ? 'Offline Cached File' : details?.state === 'playing' ? 'Live Stream (libmpv)' : 'Catalog Track',
    },
    { label: 'Play Count', value: String(details?.play_count ?? track.play_count ?? 0) },
  ];

  const sourcePath = details?.local_path || details?.path || '';

  return createPortal(
    <div
      className="fixed inset-0 z-[10000] bg-black/75 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[460px] ios-liquid-popover eh-popover-in rounded-2xl border border-white/[0.12] p-5 text-[#F4F6FF]"
      >
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="flex items-center gap-3 min-w-0">
            {track.thumbnail_url && (
              <img
                src={track.thumbnail_url}
                alt={track.title}
                className="w-12 h-12 rounded-xl object-cover flex-none"
              />
            )}
            <div className="min-w-0">
              <div className="text-[10px] font-mono uppercase tracking-[0.16em] text-[#8E99BA]">
                Audio Diagnostics & Details
              </div>
              <h3 className="text-base font-bold truncate">{details?.title || track.title}</h3>
              <p className="text-xs text-[#9AA5C4] truncate">{details?.artist || track.artist}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-full flex items-center justify-center text-[#8E99BA] hover:text-white hover:bg-white/10"
          >
            <X size={16} />
          </button>
        </div>

        {loading ? (
          <div className="py-8 text-center text-xs text-[#8E99BA]">Reading live audio stream metadata...</div>
        ) : (
          <div className="space-y-1.5 text-xs">
            {rows.map((r) => (
              <div
                key={r.label}
                className="flex items-center justify-between gap-4 py-1.5 border-b border-white/[0.06]"
              >
                <span className="text-[#8E99BA] flex-none">{r.label}</span>
                <span className="text-[#F4F6FF] font-mono text-right truncate">{r.value}</span>
              </div>
            ))}
            {sourcePath && (
              <div className="pt-2">
                <div className="flex items-center justify-between text-[11px] text-[#8E99BA] mb-1">
                  <span>Stream / File Path</span>
                  <button
                    type="button"
                    onClick={() => {
                      void navigator.clipboard?.writeText(sourcePath);
                      setCopied(true);
                      window.setTimeout(() => setCopied(false), 1500);
                    }}
                    className="inline-flex items-center gap-1 text-[10px] text-[#9AA5C4] hover:text-white"
                  >
                    <Copy size={11} />
                    <span>{copied ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
                <div className="text-[10.5px] font-mono bg-black/40 border border-white/[0.08] rounded-lg px-2.5 py-2 text-[#A6B0CF] break-all max-h-20 overflow-y-auto">
                  {sourcePath}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}

function EditTrackMetadataModal({
  track,
  onClose,
  onSave,
}: {
  track: Track;
  onClose: () => void;
  onSave: (patch: { title: string; artist: string; album: string | null }) => Promise<void>;
}) {
  const [title, setTitle] = useState(track.title || '');
  const [artist, setArtist] = useState(track.artist || '');
  const [album, setAlbum] = useState(track.album || '');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[10000] bg-black/75 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={async (e) => {
          e.preventDefault();
          if (!title.trim() || !artist.trim()) return;
          setSaving(true);
          try {
            await onSave({
              title: title.trim(),
              artist: artist.trim(),
              album: album.trim() || null,
            });
          } finally {
            setSaving(false);
          }
        }}
        className="w-full max-w-[420px] ios-liquid-popover eh-popover-in rounded-2xl border border-white/[0.12] p-5 text-[#F4F6FF]"
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-bold">Edit Track Metadata</h3>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-full flex items-center justify-center text-[#8E99BA] hover:text-white hover:bg-white/10"
          >
            <X size={16} />
          </button>
        </div>

        <div className="space-y-3 text-xs">
          <label className="block">
            <span className="text-[#8E99BA] block mb-1">Track Title</span>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              className="w-full h-9 px-3 rounded-lg bg-white/[0.06] border border-white/[0.12] text-[#F4F6FF] focus:outline-none focus:border-white/30"
            />
          </label>
          <label className="block">
            <span className="text-[#8E99BA] block mb-1">Artist(s)</span>
            <input
              type="text"
              value={artist}
              onChange={(e) => setArtist(e.target.value)}
              required
              className="w-full h-9 px-3 rounded-lg bg-white/[0.06] border border-white/[0.12] text-[#F4F6FF] focus:outline-none focus:border-white/30"
            />
          </label>
          <label className="block">
            <span className="text-[#8E99BA] block mb-1">Album</span>
            <input
              type="text"
              value={album}
              onChange={(e) => setAlbum(e.target.value)}
              placeholder="Optional album name"
              className="w-full h-9 px-3 rounded-lg bg-white/[0.06] border border-white/[0.12] text-[#F4F6FF] focus:outline-none focus:border-white/30"
            />
          </label>
        </div>

        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onClose}
            className="px-3.5 py-2 rounded-full text-xs text-[#A6B0CF] hover:text-white hover:bg-white/[0.08]"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="px-4 py-2 rounded-full text-xs font-semibold text-black bg-white hover:bg-white/90 disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </form>
    </div>,
    document.body
  );
}
