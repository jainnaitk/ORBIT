import { memo, useMemo, useState } from 'react';
import { FolderInput, ListMusic, Plus, Search, Trash2, X } from 'lucide-react';
import { bridgeApi } from '../api/client';
import type { Playlist } from '../api/types';

interface PlaylistsViewProps {
  playlists: Playlist[];
  onOpenPlaylist: (playlistId: string) => void;
  onCreatePlaylist: () => void;
  onImportPlaylist: () => void;
  onPlaylistDeleted: (playlistId: string) => void;
}

export const PlaylistsView = memo(({
  playlists,
  onOpenPlaylist,
  onCreatePlaylist,
  onImportPlaylist,
  onPlaylistDeleted,
}: PlaylistsViewProps) => {
  const [filterText, setFilterText] = useState<string>('');

  const filteredPlaylists = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    if (!q) return playlists;
    return playlists.filter(
      (pl) =>
        pl.name.toLowerCase().includes(q) ||
        (pl.description || '').toLowerCase().includes(q)
    );
  }, [playlists, filterText]);

  const handleDelete = async (e: React.MouseEvent, pl: Playlist) => {
    e.stopPropagation();
    if (!window.confirm(`Delete playlist "${pl.name}"?`)) return;
    try {
      await bridgeApi.deletePlaylist(pl.id);
      onPlaylistDeleted(pl.id);
    } catch {
      // Ignore
    }
  };

  return (
    <div
      style={{ contain: 'layout style' }}
      className="flex-1 min-w-0 flex flex-col h-full overflow-hidden select-none ios-liquid-panel rounded-[26px] my-1"
    >
      <div className="flex-none p-5 sm:p-6 border-b border-white/[0.08] bg-white/[0.02] flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3.5 min-w-0">
          <div className="w-12 h-12 rounded-2xl bg-white/[0.06] border border-white/[0.10] flex items-center justify-center flex-none">
            <ListMusic size={22} style={{ color: 'var(--dynamic-accent)' }} />
          </div>
          <div className="min-w-0">
            <span
              className="text-[10px] uppercase tracking-[0.2em] font-semibold block"
              style={{ color: 'var(--dynamic-accent)' }}
            >
              Personal Hub
            </span>
            <h1 className="text-xl sm:text-2xl font-bold text-[#F1F4FA] truncate">
              Playlists
            </h1>
            <p className="text-xs text-[#9AA5C4] mt-0.5">
              {playlists.length} playlist{playlists.length === 1 ? '' : 's'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 flex-wrap w-full sm:w-auto">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-black/40 border border-white/12 flex-1 sm:w-56">
            <Search size={12} className="text-[#8E99BA] flex-none" />
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Filter playlists..."
              className="bg-transparent text-xs text-[#F4F6FF] placeholder-[#6D7796] focus:outline-none flex-1 min-w-0"
            />
            {filterText && (
              <button
                type="button"
                onClick={() => setFilterText('')}
                className="text-[#8E99BA] hover:text-white"
              >
                <X size={12} />
              </button>
            )}
          </div>

          <button
            type="button"
            onClick={onImportPlaylist}
            className="px-3.5 py-2 ios-liquid-pill text-[#F4F6FF] text-xs font-medium rounded-full inline-flex items-center gap-1.5"
          >
            <FolderInput size={13} />
            <span>Import</span>
          </button>

          <button
            type="button"
            onClick={onCreatePlaylist}
            className="px-4 py-2 bg-[#F4F6FF] text-[#020308] text-xs font-semibold rounded-full inline-flex items-center gap-1.5 hover:scale-[1.02] active:scale-95 transition-transform shadow-md"
          >
            <Plus size={14} />
            <span>New Playlist</span>
          </button>
        </div>
      </div>

      <div className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden p-5 sm:p-6">
        {filteredPlaylists.length === 0 ? (
          <div className="py-16 text-center space-y-3 text-[#8E99BA]">
            <ListMusic size={34} className="mx-auto opacity-35" />
            <p className="text-sm font-semibold text-[#F4F6FF]">
              {filterText ? 'No matching playlists' : 'No playlists yet'}
            </p>
            <p className="text-xs max-w-sm mx-auto">
              Create a new playlist or import your existing collection from YouTube Music, Spotify, or Apple Music.
            </p>
            {!filterText && (
              <div className="pt-2 flex items-center justify-center gap-2.5 flex-wrap">
                <button
                  type="button"
                  onClick={onCreatePlaylist}
                  className="px-4 py-2 bg-[#F4F6FF] text-[#020308] text-xs font-semibold rounded-full inline-flex items-center gap-1.5"
                >
                  <Plus size={14} />
                  <span>Create Playlist</span>
                </button>
                <button
                  type="button"
                  onClick={onImportPlaylist}
                  className="px-3.5 py-2 ios-liquid-pill text-[#F4F6FF] text-xs font-medium rounded-full inline-flex items-center gap-1.5"
                >
                  <FolderInput size={13} />
                  <span>Import Playlist</span>
                </button>
              </div>
            )}
          </div>
        ) : (
          <div
            className="grid gap-3.5"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}
          >
            {filteredPlaylists.map((pl) => (
              <div
                key={pl.id}
                onClick={() => onOpenPlaylist(pl.id)}
                className="group ios-liquid-card min-w-0 rounded-2xl p-4 flex flex-col justify-between gap-3 cursor-pointer relative transition-transform hover:scale-[1.01]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div
                    className="w-12 h-12 rounded-xl flex items-center justify-center flex-none border border-white/15"
                    style={{
                      background:
                        'linear-gradient(135deg, rgba(var(--dynamic-accent-rgb), 0.24), rgba(14, 18, 30, 0.92))',
                    }}
                  >
                    <ListMusic size={20} style={{ color: 'var(--dynamic-accent)' }} />
                  </div>
                  <button
                    type="button"
                    title={`Delete "${pl.name}"`}
                    onClick={(e) => void handleDelete(e, pl)}
                    className="p-1.5 rounded-full text-[#7A84A6] hover:text-[#FF6B9E] hover:bg-white/[0.08] opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>

                <div className="min-w-0 space-y-1">
                  <h2 className="text-sm font-semibold text-[#F4F6FF] truncate">{pl.name}</h2>
                  {pl.description && (
                    <p className="text-[11px] text-[#9AA5C4] line-clamp-2">{pl.description}</p>
                  )}
                  <div className="text-[11px] font-mono tabular-nums text-[#7A84A6]">
                    {typeof pl.track_count === 'number'
                      ? `${pl.track_count} track${pl.track_count === 1 ? '' : 's'}`
                      : 'Open playlist'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});
