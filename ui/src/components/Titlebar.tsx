import { memo, useRef, useEffect, useState, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  ChevronLeft,
  ChevronRight,
  Search,
  Settings,
  Minus,
  Square,
  X,
  PictureInPicture2,
  Download,
  Loader2,
  HardDriveDownload,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';
import { OrbitLogo } from './OrbitLogo';
import { UpdateTabPopover } from './UpdateCenter';
import { minimizeWindow, toggleMaximizeWindow, closeWindow } from '../api/client';
import type { DownloadProgress, Track, UpdateState } from '../api/types';

interface TitlebarProps {
  searchQuery: string;
  onSearchChange: (query: string, replaceAll?: boolean) => void;
  onSearchSubmit: (query: string) => void;
  onSearchBlur: () => void;
  canGoBack: boolean;
  canGoForward: boolean;
  onGoBack: () => void;
  onGoForward: () => void;
  isConnected: boolean;
  onOpenSettings: () => void;
  onEnterIsland: () => void;
  onFocusSearchTab: () => void;
  updateState: UpdateState | null;
  isUpdateTabOpen: boolean;
  onToggleUpdateTab: () => void;
  onCloseUpdateTab: () => void;
  onCheckUpdates: () => void;
  onDownloadAndInstallUpdate: () => void;
  downloads?: Record<string, DownloadProgress>;
  downloadTracks?: Record<string, Track>;
  onRetryDownload?: (videoId: string) => void;
  onCancelDownload?: (videoId: string) => void;
  onClearFinishedDownloads?: (videoIds: string[]) => void;
}

export const Titlebar = memo(({
  searchQuery,
  onSearchChange,
  onSearchSubmit,
  onSearchBlur,
  canGoBack,
  canGoForward,
  onGoBack,
  onGoForward,
  isConnected,
  onOpenSettings,
  onEnterIsland,
  onFocusSearchTab,
  updateState,
  isUpdateTabOpen,
  onToggleUpdateTab,
  onCloseUpdateTab,
  onCheckUpdates,
  onDownloadAndInstallUpdate,
  downloads = {},
  downloadTracks = {},
  onRetryDownload,
  onCancelDownload,
  onClearFinishedDownloads,
}: TitlebarProps) => {
  const searchInputRef = useRef<HTMLInputElement>(null);
  const wholeQuerySelected = useRef(false);
  const downloadsPopoverRef = useRef<HTMLDivElement>(null);
  const [isDownloadsOpen, setIsDownloadsOpen] = useState(false);
  const downloadScrollRef = useRef<HTMLDivElement>(null);

  const downloadList = useMemo(
    () => Object.values(downloads).filter((d) => d.status !== 'deleted'),
    [downloads]
  );
  const activeDownloads = useMemo(
    () =>
      downloadList.filter(
        (d) => d.status === 'queued' || d.status === 'downloading' || d.status === 'tagging'
      ),
    [downloadList]
  );
  const progressRows = useVirtualizer({
    count: isDownloadsOpen ? downloadList.length : 0,
    getScrollElement: () => downloadScrollRef.current,
    estimateSize: () => 82,
    overscan: 4,
  });
  const failedDownloads = useMemo(
    () => downloadList.filter((d) => d.status === 'failed' || d.status === 'error'),
    [downloadList]
  );
  const completedDownloads = useMemo(
    () => downloadList.filter((d) => d.status === 'completed'),
    [downloadList]
  );
  const showDownloadsPill =
    activeDownloads.length > 0 || failedDownloads.length > 0 || isDownloadsOpen;

  const avgPercent = useMemo(() => {
    if (activeDownloads.length === 0) return 100;
    const sum = activeDownloads.reduce(
      (acc, d) => acc + Math.max(0, Math.min(100, d.percent ?? d.progress_pct ?? 0)),
      0
    );
    return Math.round(sum / activeDownloads.length);
  }, [activeDownloads]);

  useEffect(() => {
    if (!isDownloadsOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (
        downloadsPopoverRef.current &&
        e.target instanceof Node &&
        !downloadsPopoverRef.current.contains(e.target)
      ) {
        setIsDownloadsOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsDownloadsOpen(false);
    };
    window.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [isDownloadsOpen]);

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
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onFocusSearchTab]);

  const hasUpdate = Boolean(updateState?.update_available);
  const isDownloadingUpdate = updateState?.status === 'downloading';

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
      <div
        data-search-surface="true"
        className="flex items-center flex-1 max-w-[400px] min-w-[90px] px-2.5 sm:px-3.5 py-1.5 sm:py-2 ios-liquid-pill rounded-full text-xs no-drag focus-within:border-[rgba(var(--dynamic-accent-rgb),0.55)] transition-colors"
      >
        <Search size={13} className="mr-2 text-[#8E99BA] flex-none" />
        <input
          ref={searchInputRef}
          type="text"
          placeholder="Search..."
          onFocus={onFocusSearchTab}
          onBlur={(e) => {
            wholeQuerySelected.current = false;
            if (!(e.relatedTarget instanceof Element && e.relatedTarget.closest('[data-search-term]'))) onSearchBlur();
          }}
          onSelect={(e) => {
            const input = e.currentTarget;
            wholeQuerySelected.current = !!input.value && input.selectionStart === 0 && input.selectionEnd === input.value.length;
          }}
          value={searchQuery}
          onChange={(e) => {
            onSearchChange(e.target.value, wholeQuerySelected.current);
            wholeQuerySelected.current = false;
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) onSearchSubmit(searchQuery);
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

      {/* Right: Live Track Downloads Pill + GitHub App Update Toggle + Dynamic Island + Settings & Window Controls */}
      <div className="flex items-center no-drag gap-0.5 sm:gap-1 flex-none">
        {showDownloadsPill && (
          <div className="relative mr-1" ref={downloadsPopoverRef}>
            <button
              type="button"
              onClick={() => {
                if (isUpdateTabOpen) onCloseUpdateTab();
                setIsDownloadsOpen((v) => !v);
              }}
              title="Active Offline Track Downloads"
              className={`relative h-7 px-2.5 flex items-center gap-1.5 rounded-full text-[11px] font-medium border transition-colors overflow-hidden ${
                isDownloadsOpen
                  ? 'bg-white/[0.16] text-white border-white/25'
                  : failedDownloads.length > 0 && activeDownloads.length === 0
                  ? 'bg-[#FF4D8D]/15 text-[#FF85B3] border-[#FF4D8D]/30 hover:bg-[#FF4D8D]/25'
                  : 'bg-white/[0.07] text-[#E6ECFF] border-white/[0.12] hover:bg-white/[0.14]'
              }`}
            >
              {activeDownloads.length > 0 ? (
                <Loader2 size={12} className="animate-spin text-[#34D399]" />
              ) : failedDownloads.length > 0 ? (
                <AlertCircle size={12} className="text-[#FF85B3]" />
              ) : (
                <HardDriveDownload size={12} className="text-[#34D399]" />
              )}
              <span className="font-mono tabular-nums">
                {activeDownloads.length > 0
                  ? `${completedDownloads.length}/${completedDownloads.length + activeDownloads.length} · ${avgPercent}%`
                  : `${failedDownloads.length} failed`}
              </span>
              {activeDownloads.length > 0 && (
                <span
                  className="absolute bottom-0 left-0 right-0 h-[2px] bg-[#34D399]/80 origin-left transition-transform duration-200"
                  style={{ transform: `scaleX(${Math.max(0.05, avgPercent / 100)})` }}
                />
              )}
            </button>

            {isDownloadsOpen && (
              <div className="absolute right-0 mt-2 w-[310px] ios-liquid-popover eh-popover-in rounded-2xl border border-white/[0.12] p-3 z-[60] shadow-2xl">
                <div className="flex items-center justify-between gap-2 pb-2 mb-2 border-b border-white/[0.08]">
                  <div>
                    <div className="text-[11px] font-semibold text-[#F4F6FF]">Offline Downloads</div>
                    <div className="text-[10px] text-[#8E99BA]">
                      {activeDownloads.length} active · {completedDownloads.length} ready
                    </div>
                  </div>
                  {(completedDownloads.length > 0 || failedDownloads.length > 0) && onClearFinishedDownloads && (
                    <button
                      type="button"
                      onClick={() =>
                        onClearFinishedDownloads(
                          [...completedDownloads, ...failedDownloads].map((d) => d.video_id)
                        )
                      }
                      className="text-[10.5px] px-2 py-0.5 rounded-full bg-white/[0.06] hover:bg-white/[0.14] text-[#9AA5C4] hover:text-white transition-colors"
                    >
                      Clear Finished
                    </button>
                  )}
                </div>

                <div ref={downloadScrollRef} className="max-h-64 overflow-y-auto no-scrollbar">
                  {downloadList.length === 0 ? (
                    <div className="py-5 text-center text-[11px] text-[#8E99BA]">
                      No active downloads
                    </div>
                  ) : (
                    <div className="relative" style={{ height: progressRows.getTotalSize() }}>
                    {progressRows.getVirtualItems().map((row) => {
                      const dl = downloadList[row.index];
                      const meta = downloadTracks[dl.video_id];
                      const pct = Math.max(0, Math.min(100, Math.round(dl.percent ?? dl.progress_pct ?? 0)));
                      const isActive =
                        dl.status === 'queued' || dl.status === 'downloading' || dl.status === 'tagging';
                      const isFailed = dl.status === 'failed' || dl.status === 'error';
                      return (
                        <div
                          key={dl.video_id}
                          style={{ transform: `translateY(${row.start}px)`, height: row.size - 5 }}
                          className="absolute top-0 inset-x-0 rounded-xl bg-white/[0.04] border border-white/[0.07] p-2.5"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0 flex-1">
                              <div className="text-[12px] font-medium text-[#F4F6FF] truncate">
                                {meta?.title || dl.video_id}
                              </div>
                              <div className="text-[10.5px] text-[#8E99BA] truncate">
                                {meta?.artist || 'Track'}
                              </div>
                            </div>
                            <div className="flex items-center gap-1 flex-none">
                              {isFailed && onRetryDownload && (
                                <button
                                  type="button"
                                  title="Retry Download"
                                  onClick={() => onRetryDownload(dl.video_id)}
                                  className="w-6 h-6 rounded-full flex items-center justify-center bg-white/[0.08] hover:bg-white/[0.18] text-[#F4F6FF]"
                                >
                                  <RotateCcw size={11} />
                                </button>
                              )}
                              {isActive && onCancelDownload && (
                                <button
                                  type="button"
                                  title="Cancel Download"
                                  onClick={() => onCancelDownload(dl.video_id)}
                                  className="w-6 h-6 rounded-full flex items-center justify-center bg-white/[0.06] hover:bg-[#FF4D8D]/25 text-[#9AA5C4] hover:text-[#FF85B3]"
                                >
                                  <X size={11} />
                                </button>
                              )}
                              {dl.status === 'completed' && (
                                <CheckCircle2 size={14} className="text-[#34D399]" />
                              )}
                            </div>
                          </div>

                          <div className="mt-2 flex items-center justify-between text-[10px] font-mono text-[#8E99BA]">
                            <span className="capitalize">
                              {isFailed ? dl.error || 'Download failed' : dl.status}
                            </span>
                            {isActive && <span>{pct}%</span>}
                          </div>
                          {isActive && (
                            <div className="mt-1 h-1 rounded-full bg-white/[0.08] overflow-hidden">
                              <div
                                className="h-full bg-[#34D399] origin-left transition-transform duration-150"
                                style={{ transform: `scaleX(${Math.max(0.03, pct / 100)})` }}
                              />
                            </div>
                          )}
                        </div>
                      );
                    })}</div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="relative">
          <button
            type="button"
            onClick={() => {
              setIsDownloadsOpen(false);
              onToggleUpdateTab();
            }}
            title={
              isDownloadingUpdate
                ? `Downloading ORBIT v${updateState?.latest_version} (${Math.round(updateState?.progress_pct ?? 0)}%)`
                : hasUpdate
                ? `Update Available: v${updateState?.latest_version} — Click to open Downloads`
                : 'App Updates (GitHub)'
            }
            className={`relative w-7 h-7 sm:w-8 sm:h-8 flex items-center justify-center rounded-full transition-colors ${
              isUpdateTabOpen
                ? 'bg-white/[0.14] text-white'
                : hasUpdate
                ? 'text-[#F4F6FF] bg-white/[0.07] hover:bg-white/[0.14]'
                : 'text-[#9AA5C4] hover:text-white hover:bg-white/[0.08]'
            }`}
            style={hasUpdate ? { color: 'var(--dynamic-accent)' } : undefined}
          >
            {isDownloadingUpdate ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Download size={14} />
            )}
            {hasUpdate && !isDownloadingUpdate && (
              <span
                className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full ring-2 ring-[#06080F]"
                style={{
                  background: 'var(--dynamic-accent)',
                  boxShadow: '0 0 8px var(--dynamic-accent)',
                }}
              />
            )}
          </button>

          <UpdateTabPopover
            isOpen={isUpdateTabOpen}
            isConnected={isConnected}
            updateState={updateState}
            onClose={onCloseUpdateTab}
            onCheckUpdates={onCheckUpdates}
            onDownloadAndInstall={onDownloadAndInstallUpdate}
          />
        </div>

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
