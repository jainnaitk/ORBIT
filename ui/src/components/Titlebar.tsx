import { memo, useRef, useEffect } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Search,
  Settings,
  Minus,
  Square,
  X,
  PictureInPicture2,
} from 'lucide-react';
import { OrbitLogo } from './OrbitLogo';
import { minimizeWindow, toggleMaximizeWindow, closeWindow } from '../api/client';

interface TitlebarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onSearchSubmit: (query: string) => void;
  canGoBack: boolean;
  canGoForward: boolean;
  onGoBack: () => void;
  onGoForward: () => void;
  isConnected: boolean;
  onOpenSettings: () => void;
  onEnterIsland: () => void;
  onFocusSearchTab: () => void;
}

export const Titlebar = memo(({
  searchQuery,
  onSearchChange,
  onSearchSubmit,
  canGoBack,
  canGoForward,
  onGoBack,
  onGoForward,
  isConnected,
  onOpenSettings,
  onEnterIsland,
  onFocusSearchTab,
}: TitlebarProps) => {
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        (e.ctrlKey && e.key.toLowerCase() === 'k') ||
        (e.key === '/' &&
          document.activeElement !== searchInputRef.current &&
          document.activeElement?.tagName !== 'INPUT' &&
          document.activeElement?.tagName !== 'TEXTAREA')
      ) {
        e.preventDefault();
        onFocusSearchTab();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onFocusSearchTab]);

  return (
    <header
      data-tauri-drag-region
      className="h-12 sm:h-14 flex-none flex items-center justify-between gap-2 sm:gap-3 pl-3 sm:pl-4 pr-1.5 sm:pr-2 bg-transparent z-50 select-none titlebar-drag relative"
    >
      {/* Top-Left: Black Hole Logo + ORBIT + Nav */}
      <div className="flex items-center gap-2 sm:gap-3.5 no-drag flex-none">
        <div
          title={isConnected ? 'ORBIT • Connected' : 'ORBIT • Connecting...'}
          className="flex items-center gap-2 sm:gap-2.5"
        >
          <OrbitLogo size={28} className={isConnected ? '' : 'opacity-50 animate-pulse'} />
          <span className="hidden sm:inline font-bold text-sm tracking-[0.28em] uppercase text-[#F1F4FA]">
            ORBIT
          </span>
        </div>

        <div className="hidden md:flex items-center gap-1 ml-0.5">
          <button
            onClick={onGoBack}
            disabled={!canGoBack}
            title="Back"
            className="w-7 h-7 flex items-center justify-center rounded-full ios-liquid-pill text-[#9AA5C4] hover:text-white disabled:opacity-25 disabled:pointer-events-none transition-colors"
          >
            <ChevronLeft size={15} />
          </button>
          <button
            onClick={onGoForward}
            disabled={!canGoForward}
            title="Forward"
            className="w-7 h-7 flex items-center justify-center rounded-full ios-liquid-pill text-[#9AA5C4] hover:text-white disabled:opacity-25 disabled:pointer-events-none transition-colors"
          >
            <ChevronRight size={15} />
          </button>
        </div>
      </div>

      {/* Center: Ultra-Responsive Liquid-Glass Search Capsule */}
      <div className="flex items-center flex-1 max-w-[400px] min-w-[90px] px-2.5 sm:px-3.5 py-1.5 sm:py-2 ios-liquid-pill rounded-full text-xs no-drag focus-within:border-[rgba(var(--dynamic-accent-rgb),0.55)] transition-colors">
        <Search size={13} className="mr-2 text-[#8E99BA] flex-none" />
        <input
          ref={searchInputRef}
          type="text"
          placeholder="Search..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onSearchSubmit(searchQuery);
          }}
          className="bg-transparent text-[#F1F4FA] placeholder-[#7A84A6] text-xs focus:outline-none w-full min-w-0"
        />
        {searchQuery ? (
          <button
            type="button"
            onClick={() => onSearchChange('')}
            title="Clear search"
            className="p-0.5 ml-1 text-[#8E99BA] hover:text-white rounded-full transition-colors flex-none"
          >
            <X size={13} />
          </button>
        ) : (
          <kbd className="hidden md:inline-block text-[10px] bg-white/[0.06] px-2 py-0.5 rounded-full text-[#8E99BA] border border-white/[0.08] ml-2 font-mono flex-none">
            Ctrl+K
          </kbd>
        )}
      </div>

      {/* Right: Dynamic Island Toggle, Settings & Window Controls */}
      <div className="flex items-center no-drag gap-0.5 sm:gap-1 flex-none">
        <button
          onClick={onEnterIsland}
          title="Dynamic Island Mini-Player (Always on Top)"
          className="w-7 h-7 sm:w-8 sm:h-8 flex items-center justify-center text-[#9AA5C4] hover:text-white hover:bg-white/[0.08] rounded-full transition-colors"
        >
          <PictureInPicture2 size={14} />
        </button>

        <button
          onClick={onOpenSettings}
          title="Settings"
          className="w-7 h-7 sm:w-8 sm:h-8 flex items-center justify-center text-[#9AA5C4] hover:text-white hover:bg-white/[0.08] rounded-full transition-colors sm:mr-1"
        >
          <Settings size={14} />
        </button>

        <button
          onClick={() => void minimizeWindow()}
          title="Minimize"
          className="w-8 h-7 sm:w-9 sm:h-8 flex items-center justify-center rounded-lg text-[#9AA5C4] hover:text-white hover:bg-white/[0.08] transition-colors"
        >
          <Minus size={13} />
        </button>
        <button
          onClick={() => void toggleMaximizeWindow()}
          title="Maximize"
          className="w-8 h-7 sm:w-9 sm:h-8 flex items-center justify-center rounded-lg text-[#9AA5C4] hover:text-white hover:bg-white/[0.08] transition-colors"
        >
          <Square size={10} />
        </button>
        <button
          onClick={() => void closeWindow()}
          title="Close"
          className="w-8 h-7 sm:w-9 sm:h-8 flex items-center justify-center rounded-lg text-[#9AA5C4] hover:text-white hover:bg-[#E81123] transition-colors"
        >
          <X size={14} />
        </button>
      </div>
    </header>
  );
});
