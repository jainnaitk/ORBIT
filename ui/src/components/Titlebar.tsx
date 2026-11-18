import { useRef, useEffect } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Search,
  Settings,
  Minus,
  Square,
  X,
  Wifi,
  WifiOff,
} from 'lucide-react';
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
  onFocusSearchTab: () => void;
}

export const Titlebar = ({
  searchQuery,
  onSearchChange,
  onSearchSubmit,
  canGoBack,
  canGoForward,
  onGoBack,
  onGoForward,
  isConnected,
  onOpenSettings,
  onFocusSearchTab,
}: TitlebarProps) => {
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey && e.key.toLowerCase() === 'k') || (e.key === '/' && document.activeElement !== searchInputRef.current && (document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA'))) {
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
      className="h-11 flex-none flex items-center justify-between px-3 bg-bg-sidebar border-b border-border-subtle z-50 select-none titlebar-drag"
    >
      {/* Left: Branding, Connectivity & History Nav */}
      <div className="flex items-center gap-3 no-drag">
        <div className="flex items-center gap-2 pr-2 border-r border-border-subtle">
          <span className="w-2.5 h-2.5 rounded-full bg-accent inline-block animate-pulse"></span>
          <span className="font-semibold text-xs tracking-wider uppercase text-text-1">MetroLite</span>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={onGoBack}
            disabled={!canGoBack}
            title="Go Back"
            className="w-7 h-7 flex items-center justify-center rounded text-text-2 hover:text-text-1 hover:bg-bg-hover disabled:opacity-30 disabled:pointer-events-none transition-colors"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            onClick={onGoForward}
            disabled={!canGoForward}
            title="Go Forward"
            className="w-7 h-7 flex items-center justify-center rounded text-text-2 hover:text-text-1 hover:bg-bg-hover disabled:opacity-30 disabled:pointer-events-none transition-colors"
          >
            <ChevronRight size={16} />
          </button>
        </div>

        <div className="hidden sm:flex items-center gap-1.5 text-[11px] text-text-2 bg-bg-surface px-2 py-0.5 rounded border border-border-subtle">
          {isConnected ? (
            <>
              <Wifi size={12} className="text-emerald-400" />
              <span>127.0.0.1</span>
            </>
          ) : (
            <>
              <WifiOff size={12} className="text-amber-400 animate-pulse" />
              <span>Offline / Connecting</span>
            </>
          )}
        </div>
      </div>

      {/* Center: Global Search Bar */}
      <div className="flex items-center w-80 max-w-sm px-2.5 py-1 bg-bg-surface border border-border-subtle rounded-md text-text-2 text-xs no-drag focus-within:border-accent/60 transition-colors">
        <Search size={14} className="mr-2 text-text-2 flex-none" />
        <input
          ref={searchInputRef}
          type="text"
          placeholder="Search tracks, albums, artists..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              onSearchSubmit(searchQuery);
            }
          }}
          className="bg-transparent text-text-1 placeholder-text-2 text-xs focus:outline-none w-full"
        />
        <kbd className="text-[10px] bg-bg-hover px-1.5 py-0.5 rounded text-text-2 border border-border-subtle ml-1 font-mono">
          Ctrl+K
        </kbd>
      </div>

      {/* Right: Settings & Custom Windows Caption Controls */}
      <div className="flex items-center no-drag">
        <button
          onClick={onOpenSettings}
          title="Settings"
          className="w-8 h-8 flex items-center justify-center text-text-2 hover:text-text-1 hover:bg-bg-hover rounded transition-colors mr-1"
        >
          <Settings size={15} />
        </button>

        <div className="flex items-center">
          <button
            onClick={() => minimizeWindow()}
            title="Minimize"
            className="w-10 h-11 flex items-center justify-center text-text-2 hover:text-text-1 hover:bg-bg-hover transition-colors"
          >
            <Minus size={14} />
          </button>
          <button
            onClick={() => toggleMaximizeWindow()}
            title="Maximize"
            className="w-10 h-11 flex items-center justify-center text-text-2 hover:text-text-1 hover:bg-bg-hover transition-colors"
          >
            <Square size={12} />
          </button>
          <button
            onClick={() => closeWindow()}
            title="Close to Tray"
            className="w-11 h-11 flex items-center justify-center text-text-2 hover:text-white hover:bg-[#E81123] transition-colors"
          >
            <X size={15} />
          </button>
        </div>
      </div>
    </header>
  );
};
