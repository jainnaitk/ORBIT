import { memo } from 'react';
import {
  Home,
  Search,
  Heart,
  Download,
  Clock,
  Plus,
  ListMusic,
  PanelLeftClose,
  PanelLeftOpen,
  UsersRound,
  BarChart3,
} from 'lucide-react';
import type { Playlist } from '../api/types';

export type NavTab =
  | 'home'
  | 'search'
  | 'library'
  | 'liked'
  | 'downloads'
  | 'history'
  | 'artists'
  | 'stats'
  | 'playlist'
  | 'artist'
  | 'album';

interface SidebarProps {
  activeTab: NavTab;
  activePlaylistId: string | null;
  playlists: Playlist[];
  likedCount: number;
  downloadCount: number;
  collapsed: boolean;
  onToggleCollapse: () => void;
  onSelectTab: (tab: NavTab, playlistId?: string) => void;
  onOpenCreatePlaylist: () => void;
}

export const Sidebar = memo(({
  activeTab,
  activePlaylistId,
  playlists,
  likedCount,
  downloadCount,
  collapsed,
  onToggleCollapse,
  onSelectTab,
  onOpenCreatePlaylist,
}: SidebarProps) => {
  const railItem = (
    tab: NavTab,
    label: string,
    icon: React.ReactNode,
    badge?: number,
    activeColor?: string
  ) => {
    const active = activeTab === tab;
    const accent = activeColor || 'var(--dynamic-accent)';
    const useDynamicBg = !activeColor;

    if (collapsed) {
      return (
        <button
          key={tab}
          onClick={() => onSelectTab(tab)}
          aria-label={label}
          title={label}
          className={`relative w-full py-2 sm:py-2.5 rounded-xl sm:rounded-2xl flex flex-col items-center justify-center gap-1 transition-all duration-200 border ${
            active
              ? 'text-[#F1F4FA] shadow-[inset_0_1px_0_rgba(255,255,255,0.18)]'
              : 'border-transparent text-[#8E99BA] hover:text-white hover:bg-white/[0.06]'
          }`}
          style={
            active
              ? useDynamicBg
                ? {
                    background: 'rgba(var(--dynamic-accent-rgb), 0.14)',
                    borderColor: 'rgba(var(--dynamic-accent-rgb), 0.32)',
                  }
                : {
                    background: 'rgba(255, 255, 255, 0.11)',
                    borderColor: 'rgba(255, 255, 255, 0.14)',
                  }
              : undefined
          }
        >
          {active && (
            <span
              className="absolute left-0.5 top-2.5 bottom-2.5 sm:top-3 sm:bottom-3 w-[3px] rounded-full"
              style={{
                background: accent,
                boxShadow: `0 0 10px ${accent}`,
              }}
            />
          )}
          <div style={active ? { color: accent } : undefined}>{icon}</div>
          <span className="hidden sm:inline text-[9px] font-medium tracking-wide leading-none">
            {label}
          </span>
          {badge !== undefined && badge > 0 && (
            <span className="absolute top-0.5 right-0.5 sm:top-1 sm:right-1 text-[8px] font-mono tabular-nums bg-[#06080F]/95 text-[#E6ECF8] border border-white/15 px-1 sm:px-1.5 rounded-full leading-tight">
              {badge}
            </span>
          )}
        </button>
      );
    }

    return (
      <button
        key={tab}
        onClick={() => onSelectTab(tab)}
        aria-label={label}
        className={`relative w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all duration-200 border ${
          active
            ? 'text-[#F1F4FA] font-semibold shadow-[inset_0_1px_0_rgba(255,255,255,0.18)]'
            : 'border-transparent text-[#8E99BA] hover:bg-white/[0.06] hover:text-white'
        }`}
        style={
          active
            ? useDynamicBg
              ? {
                  background: 'rgba(var(--dynamic-accent-rgb), 0.14)',
                  borderColor: 'rgba(var(--dynamic-accent-rgb), 0.32)',
                }
              : {
                  background: 'rgba(255, 255, 255, 0.11)',
                  borderColor: 'rgba(255, 255, 255, 0.14)',
                }
            : undefined
        }
      >
        {active && (
          <span
            className="absolute left-0.5 top-2.5 bottom-2.5 w-[3px] rounded-full"
            style={{
              background: accent,
              boxShadow: `0 0 10px ${accent}`,
            }}
          />
        )}
        <div className="flex items-center gap-3">
          <span style={active ? { color: accent } : undefined}>{icon}</span>
          <span>{label}</span>
        </div>
        {badge !== undefined && badge > 0 && (
          <span className="text-[10px] font-mono tabular-nums text-[#9AA5C4] bg-white/[0.08] px-1.5 py-0.5 rounded-full">
            {badge}
          </span>
        )}
      </button>
    );
  };

  return (
    <aside
      className={`${
        collapsed ? 'w-[58px] sm:w-[76px]' : 'w-[184px] sm:w-[222px]'
      } flex-none ios-liquid-rail rounded-[20px] sm:rounded-[24px] flex flex-col p-1.5 sm:p-2 gap-1.5 sm:gap-2 select-none overflow-hidden transition-[width] duration-200 ease-[cubic-bezier(0.22,1,0.36,1)] my-1`}
    >
      {/* Primary Orbital Destinations */}
      <nav className="space-y-1">
        {railItem('home', 'Home', <Home size={17} />)}
        {railItem('search', 'Search', <Search size={17} />)}
        {railItem(
          'liked',
          'Liked',
          <Heart size={16} className={activeTab === 'liked' ? 'fill-[#FF4D8D]' : ''} />,
          likedCount,
          '#FF4D8D'
        )}
        {railItem('downloads', 'Offline', <Download size={16} />, downloadCount, '#34D399')}
        {railItem('artists', 'Artists', <UsersRound size={16} />)}
        {railItem('history', 'History', <Clock size={16} />)}
        {railItem('stats', 'Stats', <BarChart3 size={16} />)}
      </nav>

      {/* Playlists Section with Crisp Divider */}
      <div className="pt-2.5 mt-0.5 border-t border-white/[0.10] flex-1 min-h-0 flex flex-col">
        <div
          className={`flex items-center ${
            collapsed ? 'justify-center' : 'justify-between px-3'
          } mb-2`}
        >
          {!collapsed && (
            <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[#7A84A6]">
              Playlists
            </span>
          )}
          <button
            onClick={onOpenCreatePlaylist}
            title="New Playlist"
            aria-label="New Playlist"
            className="w-7 h-7 flex items-center justify-center rounded-full bg-white/[0.06] border border-white/[0.10] text-[#B2BCD9] hover:text-white hover:bg-white/[0.14] hover:border-white/20 transition-all"
          >
            <Plus size={14} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto no-scrollbar space-y-1.5">
          {playlists.length === 0 && !collapsed && (
            <button
              onClick={onOpenCreatePlaylist}
              className="w-full px-3 py-2 rounded-xl text-[11px] text-[#6D7796] hover:text-[#9AA5C4] hover:bg-white/[0.05] text-left transition-colors"
            >
              + Create your first playlist
            </button>
          )}
          {playlists.map((pl: Playlist) => {
            const isActive = activeTab === 'playlist' && activePlaylistId === pl.id;
            if (collapsed) {
              return (
                <button
                  key={pl.id}
                  onClick={() => onSelectTab('playlist', pl.id)}
                  title={pl.name}
                  className={`w-8 h-8 sm:w-10 sm:h-10 mx-auto rounded-full flex items-center justify-center text-[10px] sm:text-xs font-bold border transition-all ${
                    isActive
                      ? 'bg-white/[0.16] text-white border-[rgba(var(--dynamic-accent-rgb),0.6)]'
                      : 'bg-white/[0.05] text-[#9BA4C4] border-white/10 hover:text-white hover:bg-white/[0.12]'
                  }`}
                >
                  {pl.name.slice(0, 2).toUpperCase()}
                </button>
              );
            }
            return (
              <button
                key={pl.id}
                onClick={() => onSelectTab('playlist', pl.id)}
                className={`relative w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs truncate text-left transition-colors border ${
                  isActive
                    ? 'text-white font-semibold'
                    : 'border-transparent text-[#9BA4C4] hover:bg-white/[0.06] hover:text-white'
                }`}
                style={
                  isActive
                    ? {
                        background: 'rgba(var(--dynamic-accent-rgb), 0.14)',
                        borderColor: 'rgba(var(--dynamic-accent-rgb), 0.32)',
                      }
                    : undefined
                }
              >
                {isActive && (
                  <span
                    className="absolute left-0.5 top-2 bottom-2 w-[3px] rounded-full"
                    style={{
                      background: 'var(--dynamic-accent)',
                      boxShadow: '0 0 10px var(--dynamic-accent)',
                    }}
                  />
                )}
                <ListMusic
                  size={14}
                  className="flex-none"
                  style={isActive ? { color: 'var(--dynamic-accent)' } : undefined}
                />
                <span className="truncate">{pl.name}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Bottom Expand / Collapse Toggle */}
      <button
        onClick={onToggleCollapse}
        title={collapsed ? 'Expand Rail' : 'Collapse Rail'}
        className={`w-full py-2 rounded-xl flex items-center ${
          collapsed ? 'justify-center' : 'justify-between px-3'
        } text-[#9BA4C4] hover:text-white hover:bg-white/[0.07] transition-colors border-t border-white/[0.08]`}
      >
        {!collapsed && <span className="text-[11px]">Compact Rail</span>}
        {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
      </button>
    </aside>
  );
});
