import { memo, useEffect, useState, useCallback, useRef } from 'react';
import { Search as SearchIcon, Loader2 } from 'lucide-react';
import { TrackList } from '../components/TrackList';
import type { Track, DownloadProgress } from '../api/types';
import { bridgeApi } from '../api/client';

interface SearchViewProps {
  initialQuery?: string;
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  onPlay: (track: Track, index: number, allTracks?: Track[]) => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onDeleteDownload: (track: Track) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist: (track: Track) => void;
}

const FILTER_TYPES = [
  { id: 'songs', label: 'Songs' },
  { id: 'videos', label: 'Videos' },
  { id: 'all', label: 'All Tracks' },
];

export const SearchView = memo(({
  initialQuery = '',
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
}: SearchViewProps) => {
  const [filterType, setFilterType] = useState<string>('songs');
  const [results, setResults] = useState<Track[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [settledKey, setSettledKey] = useState<string>('');
  const debounceTimerRef = useRef<number | null>(null);
  const reqSeqRef = useRef<number>(0);

  const trimmedQuery = initialQuery.trim();
  const activeKey = `${trimmedQuery}::${filterType}`;

  const executeSearch = useCallback(async (q: string, filter: string, key: string) => {
    const mySeq = ++reqSeqRef.current;
    if (!q) {
      setResults([]);
      setIsLoading(false);
      setSettledKey(key);
      return;
    }
    setIsLoading(true);
    try {
      const data = await bridgeApi.search(q, filter, 30);
      if (mySeq !== reqSeqRef.current) return;
      setResults(data);
    } catch {
      if (mySeq !== reqSeqRef.current) return;
      setResults([]);
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
      setIsLoading(false);
      setSettledKey(activeKey);
      return;
    }
    setIsLoading(true);
    debounceTimerRef.current = window.setTimeout(() => {
      void executeSearch(trimmedQuery, filterType, activeKey);
    }, 240);
    return () => {
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    };
  }, [trimmedQuery, filterType, activeKey, executeSearch]);

  const showSkeleton = isLoading || (Boolean(trimmedQuery) && settledKey !== activeKey);

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden select-none ios-liquid-panel eh-page-in rounded-[26px] my-1">
      <div className="flex-none px-5 py-4 border-b border-white/[0.08] bg-white/[0.02] flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <SearchIcon size={16} style={{ color: 'var(--dynamic-accent)' }} className="flex-none" />
            <h1 className="text-base sm:text-lg font-bold text-[#F4F6FF] truncate">
              {trimmedQuery ? `Results for “${trimmedQuery}”` : 'Search ORBIT'}
            </h1>
          </div>
          {showSkeleton ? (
            <div className="flex items-center gap-1.5 text-[11px] text-[#A6B0CF] flex-none">
              <Loader2 size={14} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
              <span>Searching...</span>
            </div>
          ) : trimmedQuery && results.length > 0 ? (
            <span className="text-[11px] font-mono tabular-nums text-[#8E99BA] flex-none">
              {results.length} matches
            </span>
          ) : null}
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
      </div>

      <TrackList
        tracks={showSkeleton && results.length === 0 ? [] : results}
        loading={showSkeleton}
        currentTrackId={currentTrackId}
        isPlaying={isPlaying}
        downloads={downloads}
        likedIds={likedIds}
        downloadedIds={downloadedIds}
        onPlay={(track, index) => onPlay(track, index, results)}
        onToggleLike={onToggleLike}
        onDownload={onDownload}
        onDeleteDownload={onDeleteDownload}
        onAddNext={onAddNext}
        onAppendQueue={onAppendQueue}
        onOpenAddToPlaylist={onOpenAddToPlaylist}
        emptyMessage={
          trimmedQuery
            ? 'No matches found. Try another search query.'
            : 'Type in the top search bar (Ctrl+K) to find songs, albums, and artists.'
        }
      />
    </div>
  );
});
