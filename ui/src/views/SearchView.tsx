import { memo, useEffect, useState, useCallback, useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Search as SearchIcon, Loader2, X, RefreshCw } from 'lucide-react';
import { TrackList } from '../components/TrackList';
import { useTwoFingerClose } from '../utils/gestures';
import type { Track, DownloadProgress } from '../api/types';
import { bridgeApi, bridgeSocket } from '../api/client';

interface SearchViewProps {
  initialQuery?: string;
  isConnected?: boolean;
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  onPlay: (track: Track, index: number, allTracks?: Track[]) => Promise<boolean>;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onDeleteDownload: (track: Track) => void;
  onAddNext: (track: Track) => Promise<boolean>;
  onAppendQueue: (track: Track) => Promise<boolean>;
  onOpenAddToPlaylist: (track: Track) => void;
  onOpenArtist?: (artistName: string, artistId?: string | null) => void;
  onOpenAlbum?: (albumName: string, albumId: string | null | undefined, track: Track) => void;
  onCloseSearch?: () => void;
  onSelectTerm: (term: string) => void;
}

const FILTER_TYPES = [
  { id: 'songs', label: 'Songs' },
  { id: 'videos', label: 'Videos' },
  { id: 'all', label: 'All Tracks' },
];

export const SearchView = memo(({
  initialQuery = '',
  isConnected = true,
  currentTrackId,
  isPlaying,
  downloads = {},
  likedIds,
  downloadedIds,
  onPlay,
  onToggleLike,
  onDownload,
  onDeleteDownload,
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
  onOpenArtist,
  onOpenAlbum,
  onCloseSearch,
  onSelectTerm,
}: SearchViewProps) => {
  const [filterType, setFilterType] = useState<string>('songs');
  const [results, setResults] = useState<Track[]>([]);
  const [history, setHistory] = useState<{ terms: string[]; tracks: Track[] }>({ terms: [], tracks: [] });
  useEffect(() => {
    let revision = 0;
    const refresh = async () => {
      const mine = ++revision;
      try {
        const data = await bridgeApi.getSearchHistory();
        if (mine === revision) setHistory(data);
      } catch { /* The bridge may still be connecting. */ }
    };
    void refresh();
    const unsubscribe = bridgeSocket.on('search_history_changed', refresh);
    return () => { revision++; unsubscribe(); };
  }, [isConnected]);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [settledKey, setSettledKey] = useState<string>('');
  const debounceTimerRef = useRef<number | null>(null);
  const reqSeqRef = useRef<number>(0);
  const prevConnectedRef = useRef<boolean>(isConnected);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleClose = useCallback(() => {
    onCloseSearch?.();
  }, [onCloseSearch]);

  const trimmedQuery = initialQuery.trim();
  const activeKey = `${trimmedQuery}::${filterType}`;

  const executeSearch = useCallback(async (q: string, filter: string, key: string) => {
    const mySeq = ++reqSeqRef.current;
    if (!q) {
      setResults([]);
      setSearchError(null);
      setIsLoading(false);
      setSettledKey(key);
      return;
    }
    setIsLoading(true);
    setSearchError(null);
    try {
      const data = await bridgeApi.search(q, filter, 30);
      if (mySeq !== reqSeqRef.current) return;
      setResults(data);
      setSearchError(null);
    } catch (err: any) {
      if (mySeq !== reqSeqRef.current) return;
      setResults([]);
      setSearchError(err?.message || 'Unable to reach YouTube Music');
    } finally {
      if (mySeq === reqSeqRef.current) {
        setIsLoading(false);
        setSettledKey(key);
      }
    }
  }, []);

  useEffect(() => {
    if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    if (!trimmedQuery) {
      reqSeqRef.current += 1;
      setResults([]);
      setSearchError(null);
      setIsLoading(false);
      setSettledKey(activeKey);
      return;
    }
    setIsLoading(true);
    setSearchError(null);
    debounceTimerRef.current = window.setTimeout(() => {
      void executeSearch(trimmedQuery, filterType, activeKey);
    }, 240);
    return () => {
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    };
  }, [trimmedQuery, filterType, activeKey, executeSearch]);

  useEffect(() => {
    const wasConnected = prevConnectedRef.current;
    prevConnectedRef.current = isConnected;
    if (!wasConnected && isConnected && trimmedQuery && searchError && !isLoading) {
      void executeSearch(trimmedQuery, filterType, activeKey);
    }
  }, [isConnected, trimmedQuery, filterType, activeKey, searchError, isLoading, executeSearch]);

  const showSkeleton = isLoading || (Boolean(trimmedQuery) && settledKey !== activeKey);
  const shownTracks = trimmedQuery ? results : history.tracks;
  const prefix = trimmedQuery.toLocaleLowerCase();
  const suggestions = history.terms.filter(term => term.toLocaleLowerCase().startsWith(prefix));
  const termsRef = useRef<HTMLDivElement>(null);
  const termList = useVirtualizer({
    horizontal: true,
    count: suggestions.length,
    getScrollElement: () => termsRef.current,
    getItemKey: index => suggestions[index],
    estimateSize: () => 124,
    gap: 8,
  });
  useEffect(() => { if (termsRef.current) termsRef.current.scrollLeft = 0; }, [prefix]);

  const rememberSelection = async (track: Track, action: Promise<boolean>) => {
    if (await action) await bridgeApi.rememberSearch({ track }).catch(() => {});
  };

  useTwoFingerClose({
    enabled: Boolean(onCloseSearch),
    ready: !showSkeleton && !isLoading,
    settleMs: 500,
    containerRef,
    onClose: handleClose,
  });

  return (
    <div
      ref={containerRef}
      data-search-surface="true"
      className="flex-1 flex flex-col h-full overflow-hidden select-none ios-liquid-panel rounded-[26px] my-1"
    >
      <div className="flex-none px-5 py-4 border-b border-white/[0.08] bg-white/[0.02] flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <SearchIcon size={16} style={{ color: 'var(--dynamic-accent)' }} className="flex-none" />
            <h1 className="text-base sm:text-lg font-bold text-[#F4F6FF] truncate">
              {trimmedQuery ? `Results for “${trimmedQuery}”` : 'Search ORBIT'}
            </h1>
          </div>
          <div className="flex items-center gap-2 flex-none">
            {showSkeleton ? (
              <div className="flex items-center gap-1.5 text-[11px] text-[#A6B0CF]">
                <Loader2 size={14} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
                <span>Searching...</span>
              </div>
            ) : trimmedQuery && results.length > 0 ? (
              <span className="text-[11px] font-mono tabular-nums text-[#8E99BA]">
                {results.length} matches
              </span>
            ) : null}
            {onCloseSearch && (
              <button
                type="button"
                onClick={handleClose}
                title="Close Search (Two-finger swipe down or tap outside)"
                className="p-1.5 rounded-full text-[#9AA5C4] hover:text-white hover:bg-white/[0.10] transition-colors"
              >
                <X size={14} />
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 overflow-x-auto pb-0.5">
          {FILTER_TYPES.map((chip) => (
            <button
              key={chip.id}
              onClick={() => setFilterType(chip.id)}
              className={`px-3.5 py-1 rounded-full text-xs font-medium transition-all ${
                filterType === chip.id
                  ? 'bg-[#F1F4FA] text-[#06080F] font-semibold shadow-sm'
                  : 'ios-liquid-pill text-[#9AA5C4] hover:text-white'
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>
        {suggestions.length > 0 && (
          <div className="flex items-center gap-2 text-xs">
            <span className="shrink-0 text-[#9AA5C4]">Recent searches</span>
            <div ref={termsRef} role="region" aria-label="Recent search terms" tabIndex={0} className="min-w-0 flex-1 overflow-x-auto">
              <div className="relative h-8" style={{ width: termList.getTotalSize() }}>
                {termList.getVirtualItems().map(item => {
                  const term = suggestions[item.index];
                  return (
                    <button key={term} data-index={item.index} ref={termList.measureElement} type="button" title={term}
                      className="absolute left-0 top-0 w-max max-w-48 truncate ios-liquid-pill rounded-full px-3 py-1 text-[#E6ECF8]"
                      style={{ transform: `translateX(${item.start}px)` }} onClick={() => onSelectTerm(term)}>
                      {term}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>

      {!trimmedQuery && history.tracks.length > 0 && <h2 className="px-5 py-3 text-xs font-semibold text-[#A6B0CF]">Recently played or queued from search</h2>}

      {!showSkeleton && searchError && results.length === 0 ? (
        <div
          onClick={() => handleClose()}
          className="flex-1 flex flex-col items-center justify-center p-8 text-center gap-3"
        >
          <SearchIcon size={28} className="text-[#6D7796]" />
          <div onClick={(e) => e.stopPropagation()} className="max-w-sm">
            <p className="text-sm font-semibold text-[#F1F4FA]">Search couldn&apos;t load results</p>
            <p className="text-xs text-[#9AA5C4] mt-1">{searchError}</p>
          </div>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              void executeSearch(trimmedQuery, filterType, activeKey);
            }}
            className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-semibold bg-[#F1F4FA] text-[#06080F] hover:opacity-90 transition-opacity"
          >
            <RefreshCw size={12} />
            Retry
          </button>
        </div>
      ) : (
        <TrackList
          tracks={trimmedQuery && showSkeleton ? [] : shownTracks}
          loading={Boolean(trimmedQuery) && showSkeleton}
          scrollKey={activeKey}
          currentTrackId={currentTrackId}
          isPlaying={isPlaying}
          downloads={downloads}
          likedIds={likedIds}
          downloadedIds={downloadedIds}
          onPlay={(track, index) => void rememberSelection(track, onPlay(track, index, shownTracks))}
          onToggleLike={onToggleLike}
          onDownload={onDownload}
          onDeleteDownload={onDeleteDownload}
          onAddNext={track => void rememberSelection(track, onAddNext(track))}
          onAppendQueue={track => void rememberSelection(track, onAppendQueue(track))}
          onOpenAddToPlaylist={onOpenAddToPlaylist}
          onOpenArtist={onOpenArtist}
          onOpenAlbum={onOpenAlbum}
          onEmptyAreaClick={handleClose}
          emptyMessage={
            trimmedQuery
              ? 'No matches found. Try another search query.'
              : 'Type in the top search bar (Ctrl+K) to find songs, albums, and artists.'
          }
        />
      )}
    </div>
  );
});
