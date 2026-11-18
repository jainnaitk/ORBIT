import {
  Home,
  Search,
  Library,
  Heart,
  Download,
  Clock,
  Plus,
  ListMusic,
} from 'lucide-react';
import type { Playlist } from '../api/types';

export type NavTab = 'home' | 'search' | 'library' | 'liked' | 'downloads' | 'history' | 'playlist';

interface SidebarProps {
  activeTab: NavTab;
  activePlaylistId: string | null;
  playlists: Playlist[];
  likedCount: number;
  downloadCount: number;
  onSelectTab: (tab: NavTab, playlistId?: string) => void;
  onOpenCreatePlaylist: () => void;
}

export const Sidebar = ({
  activeTab,
  activePlaylistId,
  playlists,
  likedCount,
  downloadCount,
  onSelectTab,
  onOpenCreatePlaylist,
}: SidebarProps) => {
  return (
    <aside className="w-[220px] flex-none bg-bg-sidebar border-r border-border-subtle flex flex-col p-3 gap-4 select-none overflow-hidden">
      {/* Primary Navigation */}
      <nav className="space-y-1">
        <button
          onClick={() => onSelectTab('home')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
            activeTab === 'home'
              ? 'bg-bg-hover text-accent font-semibold'
              : 'text-text-2 hover:bg-bg-hover hover:text-text-1'
          }`}
        >
          <Home size={16} />
          <span>Home</span>
        </button>

        <button
          onClick={() => onSelectTab('search')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
            activeTab === 'search'
              ? 'bg-bg-hover text-accent font-semibold'
              : 'text-text-2 hover:bg-bg-hover hover:text-text-1'
          }`}
        >
          <Search size={16} />
          <span>Search</span>
        </button>

        <button
          onClick={() => onSelectTab('library')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
            activeTab === 'library'
              ? 'bg-bg-hover text-accent font-semibold'
              : 'text-text-2 hover:bg-bg-hover hover:text-text-1'
          }`}
        >
          <Library size={16} />
          <span>Library</span>
        </button>
      </nav>

      {/* Library Section */}
      <div className="pt-2 border-t border-border-subtle">
        <div className="text-[11px] font-semibold text-text-2 uppercase tracking-wider px-3 mb-1.5">
          My Collection
        </div>
        <div className="space-y-1">
          <button
            onClick={() => onSelectTab('liked')}
            className={`w-full flex items-center justify-between px-3 py-2 rounded-md text-xs font-medium transition-colors ${
              activeTab === 'liked'
                ? 'bg-bg-hover text-liked font-semibold'
                : 'text-text-2 hover:bg-bg-hover hover:text-text-1'
            }`}
          >
            <div className="flex items-center gap-3">
              <Heart
                size={16}
                className={activeTab === 'liked' ? 'fill-liked text-liked' : ''}
              />
              <span>Liked Songs</span>
            </div>
            {likedCount > 0 && (
              <span className="text-[10px] font-mono text-text-2 bg-bg-surface px-1.5 py-0.2 rounded border border-border-subtle">
                {likedCount}
              </span>
            )}
          </button>

          <button
            onClick={() => onSelectTab('downloads')}
            className={`w-full flex items-center justify-between px-3 py-2 rounded-md text-xs font-medium transition-colors ${
              activeTab === 'downloads'
                ? 'bg-bg-hover text-downloaded font-semibold'
                : 'text-text-2 hover:bg-bg-hover hover:text-text-1'
            }`}
          >
            <div className="flex items-center gap-3">
              <Download size={16} />
              <span>Downloads</span>
            </div>
            {downloadCount > 0 && (
              <span className="text-[10px] font-mono text-downloaded bg-downloaded/10 px-1.5 py-0.2 rounded border border-downloaded/20">
                {downloadCount}
              </span>
            )}
          </button>

          <button
            onClick={() => onSelectTab('history')}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
              activeTab === 'history'
                ? 'bg-bg-hover text-accent font-semibold'
                : 'text-text-2 hover:bg-bg-hover hover:text-text-1'
            }`}
          >
            <Clock size={16} />
            <span>History</span>
          </button>
        </div>
      </div>

      {/* Playlists Section */}
      <div className="flex-1 min-h-0 flex flex-col pt-2 border-t border-border-subtle">
        <div className="flex items-center justify-between px-3 mb-1.5">
          <span className="text-[11px] font-semibold text-text-2 uppercase tracking-wider">
            Playlists
          </span>
          <button
            onClick={onOpenCreatePlaylist}
            title="Create New Playlist"
            className="w-5 h-5 flex items-center justify-center text-text-2 hover:text-text-1 hover:bg-bg-hover rounded transition-colors"
          >
            <Plus size={14} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto space-y-0.5 pr-1">
          {playlists.length === 0 ? (
            <div className="px-3 py-3 text-xs text-text-2/60 italic">
              No playlists yet. Click + to create one.
            </div>
          ) : (
            playlists.map((pl: Playlist) => {
              const isActive = activeTab === 'playlist' && activePlaylistId === pl.id;
              return (
                <button
                  key={pl.id}
                  onClick={() => onSelectTab('playlist', pl.id)}
                  className={`w-full flex items-center gap-2.5 px-3 py-1.5 rounded-md text-xs truncate text-left transition-colors ${
                    isActive
                      ? 'bg-bg-hover text-accent font-semibold'
                      : 'text-text-2 hover:bg-bg-hover hover:text-text-1'
                  }`}
                >
                  <ListMusic size={14} className="flex-none" />
                  <span className="truncate">{pl.name}</span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </aside>
  );
};
