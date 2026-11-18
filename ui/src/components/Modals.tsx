import { useState, useEffect } from 'react';
import { X, Plus, ListMusic, HardDrive, ShieldCheck, SunMoon } from 'lucide-react';
import type { Playlist, Track, BridgeConfig } from '../api/types';

interface CreatePlaylistModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (name: string, description: string) => void;
}

export const CreatePlaylistModal: React.FC<CreatePlaylistModalProps> = ({
  isOpen,
  onClose,
  onCreate,
}) => {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  useEffect(() => {
    if (isOpen) {
      setName('');
      setDescription('');
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    onCreate(name.trim(), description.trim());
    onClose();
  };

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md bg-bg-surface border border-border-subtle rounded-xl shadow-2xl p-6 space-y-4"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-text-1">New Playlist</h2>
          <button
            onClick={onClose}
            className="p-1 text-text-2 hover:text-text-1 rounded hover:bg-bg-hover transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-text-2 mb-1.5">
              Playlist Name
            </label>
            <input
              type="text"
              required
              autoFocus
              placeholder="e.g. Chill Beats, Workout Motivation"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs text-text-1 placeholder-text-2/60 focus:outline-none focus:border-accent"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-text-2 mb-1.5">
              Description (optional)
            </label>
            <textarea
              rows={3}
              placeholder="Give your playlist a vibe or theme"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs text-text-1 placeholder-text-2/60 focus:outline-none focus:border-accent resize-none"
            />
          </div>

          <div className="flex items-center justify-end gap-2.5 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg text-xs font-medium text-text-2 hover:bg-bg-hover hover:text-text-1 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!name.trim()}
              className="px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-bg-base hover:opacity-95 disabled:opacity-40 transition-opacity"
            >
              Create Playlist
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

interface AddToPlaylistModalProps {
  isOpen: boolean;
  track: Track | null;
  playlists: Playlist[];
  onClose: () => void;
  onSelectPlaylist: (playlistId: string, track: Track) => void;
  onOpenCreatePlaylist: () => void;
}

export const AddToPlaylistModal: React.FC<AddToPlaylistModalProps> = ({
  isOpen,
  track,
  playlists,
  onClose,
  onSelectPlaylist,
  onOpenCreatePlaylist,
}) => {
  if (!isOpen || !track) return null;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm bg-bg-surface border border-border-subtle rounded-xl shadow-2xl p-5 space-y-4"
      >
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-bold text-text-1">Add to Playlist</h2>
            <p className="text-[11px] text-text-2 truncate max-w-[240px] mt-0.5">
              {track.title}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-text-2 hover:text-text-1 rounded hover:bg-bg-hover transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <div className="max-h-60 overflow-y-auto space-y-1 pr-1">
          <button
            onClick={() => {
              onClose();
              onOpenCreatePlaylist();
            }}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium text-accent hover:bg-bg-hover transition-colors border border-dashed border-accent/40 mb-2"
          >
            <Plus size={14} />
            <span>Create New Playlist</span>
          </button>

          {playlists.length === 0 ? (
            <div className="py-4 text-center text-xs text-text-2 italic">
              No playlists found. Create one above!
            </div>
          ) : (
            playlists.map((pl) => (
              <button
                key={pl.id}
                onClick={() => {
                  onSelectPlaylist(pl.id, track);
                  onClose();
                }}
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs text-text-1 hover:bg-bg-hover transition-colors"
              >
                <div className="flex items-center gap-2.5 truncate">
                  <ListMusic size={14} className="text-text-2 flex-none" />
                  <span className="truncate">{pl.name}</span>
                </div>
                <span className="text-[10px] font-mono text-text-2 ml-2 flex-none">
                  {pl.track_count || 0} tracks
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  config: BridgeConfig | null;
  isOled: boolean;
  onToggleOled: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  config,
  isOled,
  onToggleOled,
}) => {
  if (!isOpen) return null;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md bg-bg-surface border border-border-subtle rounded-xl shadow-2xl p-6 space-y-5"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-text-1">Settings & Information</h2>
          <button
            onClick={onClose}
            className="p-1 text-text-2 hover:text-text-1 rounded hover:bg-bg-hover transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4 text-xs">
          {/* Theme selection */}
          <div className="flex items-center justify-between p-3 rounded-lg bg-bg-base border border-border-subtle">
            <div className="flex items-center gap-3">
              <SunMoon size={18} className="text-accent" />
              <div>
                <div className="font-semibold text-text-1">Pure Black OLED Mode</div>
                <div className="text-[11px] text-text-2">
                  True #000000 black canvas for OLED displays
                </div>
              </div>
            </div>
            <button
              onClick={onToggleOled}
              className={`w-9 h-5 rounded-full p-0.5 transition-colors ${
                isOled ? 'bg-accent' : 'bg-bg-hover border border-border-subtle'
              }`}
            >
              <div
                className={`w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                  isOled ? 'translate-x-4' : 'translate-x-0'
                }`}
              />
            </button>
          </div>

          {/* Local Bridge Status */}
          <div className="p-3 rounded-lg bg-bg-base border border-border-subtle space-y-2">
            <div className="flex items-center gap-2 font-semibold text-text-1">
              <ShieldCheck size={16} className="text-emerald-400" />
              <span>Bridge Security & Binding</span>
            </div>
            <div className="text-[11px] text-text-2 space-y-1">
              <div>Bound to: <span className="font-mono text-text-1">127.0.0.1:{config?.port || 8766}</span></div>
              <div>Auth Token: <span className="text-emerald-400 font-mono">Protected & Secured</span></div>
            </div>
          </div>

          {/* Storage Paths */}
          <div className="p-3 rounded-lg bg-bg-base border border-border-subtle space-y-2">
            <div className="flex items-center gap-2 font-semibold text-text-1">
              <HardDrive size={16} className="text-accent" />
              <span>Offline Database & Downloads</span>
            </div>
            <div className="text-[11px] font-mono text-text-2 break-all">
              %LOCALAPPDATA%\metrolite\
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-bg-base hover:opacity-95 transition-opacity"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};
