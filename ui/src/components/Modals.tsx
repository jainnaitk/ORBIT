import { useState, useEffect, type FC, type FormEvent } from 'react';
import { X, Plus, ListMusic, Sparkles, Download, AlignLeft } from 'lucide-react';
import { OrbitLogo } from './OrbitLogo';
import type { CosmicEffectsMode } from './StarfieldBg';
import type { Playlist, Track } from '../api/types';

interface CreatePlaylistModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (name: string, description: string) => void;
}

export const CreatePlaylistModal: FC<CreatePlaylistModalProps> = ({
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

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    onCreate(name.trim(), description.trim());
    onClose();
  };

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 bg-black/75 eh-modal-backdrop z-50 flex items-center justify-center p-4 select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md ios-liquid-popover eh-modal-in rounded-[26px] p-6 space-y-4"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-[#F4F6FF]">New Playlist</h2>
          <button
            onClick={onClose}
            className="p-1 text-[#9BA4C4] hover:text-white rounded-lg hover:bg-white/10 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-[#9AA5C4] mb-1.5">
              Playlist Name
            </label>
            <input
              type="text"
              required
              autoFocus
              placeholder="e.g. Late Night Orbit, Deep Focus"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-[#06080F] border border-white/10 rounded-xl px-3.5 py-2.5 text-xs text-[#F1F4FA] placeholder-[#7A84A6] focus:outline-none focus:border-[rgba(var(--dynamic-accent-rgb),0.55)]"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[#9AA5C4] mb-1.5">
              Description (optional)
            </label>
            <textarea
              rows={3}
              placeholder="Give your playlist a vibe"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full bg-[#06080F] border border-white/10 rounded-xl px-3.5 py-2.5 text-xs text-[#F1F4FA] placeholder-[#7A84A6] focus:outline-none focus:border-[rgba(var(--dynamic-accent-rgb),0.55)] resize-none"
            />
          </div>

          <div className="flex items-center justify-end gap-2.5 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-full text-xs font-medium text-[#9BA4C4] hover:bg-white/10 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!name.trim()}
              className="px-4 py-2 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              Create
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

export const AddToPlaylistModal: FC<AddToPlaylistModalProps> = ({
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
      className="fixed inset-0 bg-black/75 eh-modal-backdrop z-50 flex items-center justify-center p-4 select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm ios-liquid-popover eh-modal-in rounded-[26px] p-5 space-y-4"
      >
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-bold text-[#F4F6FF]">Add to Playlist</h2>
            <p className="text-[11px] text-[#9BA4C4] truncate max-w-[240px] mt-0.5">
              {track.title}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-[#9BA4C4] hover:text-white rounded-lg hover:bg-white/10 transition-colors"
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
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-medium text-[#F4F6FF] hover:bg-white/10 transition-colors border border-dashed border-white/20 mb-2"
          >
            <Plus size={14} style={{ color: 'var(--dynamic-accent)' }} />
            <span>Create New Playlist</span>
          </button>

          {playlists.length === 0 ? (
            <div className="py-4 text-center text-xs text-[#5E6685] italic">
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
                className="w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs text-[#F4F6FF] hover:bg-white/10 transition-colors"
              >
                <div className="flex items-center gap-2.5 truncate">
                  <ListMusic size={14} className="text-[#9BA4C4] flex-none" />
                  <span className="truncate">{pl.name}</span>
                </div>
                <span className="text-[10px] font-mono tabular-nums text-[#9BA4C4] ml-2 flex-none">
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
  cosmicMode: CosmicEffectsMode;
  onChangeCosmicMode: (mode: CosmicEffectsMode) => void;
  autoDownloadLiked: boolean;
  onToggleAutoDownloadLiked: () => void;
  lyricsEdgeBlur: boolean;
  onToggleLyricsEdgeBlur: () => void;
}

const COSMIC_MODES: { id: CosmicEffectsMode; label: string; desc: string }[] = [
  { id: 'full', label: 'Full', desc: 'Parallax stars, nebula & halo' },
  { id: 'static', label: 'Static Stars', desc: 'Zero animation, crisp depth' },
  { id: 'oled', label: 'Minimal OLED', desc: 'True #000000 black with static stars' },
];

export const SettingsModal: FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  cosmicMode,
  onChangeCosmicMode,
  autoDownloadLiked,
  onToggleAutoDownloadLiked,
  lyricsEdgeBlur,
  onToggleLyricsEdgeBlur,
}) => {
  if (!isOpen) return null;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 bg-black/75 eh-modal-backdrop z-50 flex items-center justify-center p-4 select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md ios-liquid-popover eh-modal-in rounded-[26px] p-6 space-y-5"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-[#F4F6FF]">Settings</h2>
          <button
            onClick={onClose}
            className="p-1 text-[#9BA4C4] hover:text-white rounded-lg hover:bg-white/10 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <div className="space-y-3.5 text-xs">
          {/* Cosmic Effects Power / Appearance Control */}
          <div className="p-3.5 rounded-xl bg-white/[0.04] border border-white/10 space-y-3">
            <div className="flex items-center gap-2.5">
              <Sparkles size={15} style={{ color: 'var(--dynamic-accent)' }} />
              <div>
                <div className="font-semibold text-[#F4F6FF]">Cosmic Effects</div>
                <div className="text-[11px] text-[#9BA4C4]">
                  Auto-pauses when minimized or unfocused to save RAM & battery
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-1.5">
              {COSMIC_MODES.map((m) => {
                const active = cosmicMode === m.id;
                return (
                  <button
                    key={m.id}
                    onClick={() => onChangeCosmicMode(m.id)}
                    title={m.desc}
                    className={`py-2 px-2 rounded-lg text-[11px] font-medium border transition-colors ${
                      active
                        ? 'bg-[#F4F6FF] text-[#020308] border-[#F4F6FF] font-semibold'
                        : 'bg-black/40 text-[#9BA4C4] border-white/10 hover:text-white hover:bg-white/[0.08]'
                    }`}
                  >
                    {m.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Lyrics Progressive Edge Blur */}
          <div className="flex items-center justify-between p-3.5 rounded-xl bg-white/[0.04] border border-white/10">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-black/50 border border-white/10 flex items-center justify-center flex-none">
                <AlignLeft size={16} style={{ color: 'var(--dynamic-accent)' }} />
              </div>
              <div>
                <div className="font-semibold text-[#F4F6FF]">Lyrics Edge Blur</div>
                <div className="text-[11px] text-[#9AA5C4] mt-0.5">
                  Softly blur distant lines on the synced lyrics stage
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={onToggleLyricsEdgeBlur}
              aria-pressed={lyricsEdgeBlur}
              className={`w-10 h-5 rounded-full p-0.5 transition-colors flex-none ${
                lyricsEdgeBlur ? 'bg-[var(--dynamic-accent)]' : 'bg-white/10 border border-white/15'
              }`}
            >
              <div
                className={`w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                  lyricsEdgeBlur ? 'translate-x-5' : 'translate-x-0'
                }`}
              />
            </button>
          </div>

          {/* Auto-Download Liked Songs */}
          <div className="flex items-center justify-between p-3.5 rounded-xl bg-white/[0.04] border border-white/10">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-black/50 border border-white/10 flex items-center justify-center flex-none">
                <Download size={16} className="text-[#34D399]" />
              </div>
              <div>
                <div className="font-semibold text-[#F4F6FF]">Auto-Download Liked Songs</div>
                <div className="text-[11px] text-[#9BA4C4] mt-0.5">
                  Save newly liked tracks automatically for offline playback
                </div>
              </div>
            </div>
            <button
              onClick={onToggleAutoDownloadLiked}
              className={`w-10 h-5 rounded-full p-0.5 transition-colors flex-none ${
                autoDownloadLiked ? 'bg-[#34D399]' : 'bg-white/10 border border-white/15'
              }`}
            >
              <div
                className={`w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                  autoDownloadLiked ? 'translate-x-5' : 'translate-x-0'
                }`}
              />
            </button>
          </div>

          {/* Creator Credit */}
          <div className="p-3.5 rounded-xl bg-black/50 border border-white/10 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <OrbitLogo size={28} />
              <div>
                <div className="text-xs font-bold tracking-[0.22em] text-[#F4F6FF]">ORBIT</div>
                <div className="text-[11px] text-[#9BA4C4] mt-0.5">Created by Naitik Jain</div>
              </div>
            </div>
            <span className="text-[10px] font-mono text-[#9BA4C4] bg-white/[0.06] px-2.5 py-0.5 rounded-full border border-white/10">
              Event Horizon
            </span>
          </div>
        </div>

        <div className="flex items-center justify-end pt-1">
          <button
            onClick={onClose}
            className="px-5 py-2 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90 transition-opacity"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};
