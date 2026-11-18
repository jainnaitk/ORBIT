import { useEffect, useState, useCallback, useRef } from 'react';
import { Search as SearchIcon, Loader2 } from 'lucide-react';
import { TrackList } from '../components/TrackList';
import type { Track, DownloadProgress } from '../api/types';
import { bridgeApi } from '../api/client';

interface SearchViewProps {
  initialQuery?: string;
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  onPlay: (track: Track, index: number, allTracks: Track[]) => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist: (track: Track) => void;
}

const FILTER_TYPES = [
  { id: 'songs', label: 'Songs' },
  { id: 'albums', label: 'Albums' },
  { id: 'playlists', label: 'Playlists' },
  { id: 'artists', label: 'Artists' },
  { id: 'all', label: 'All' },
];

export const SearchView = ({
  initialQuery = '',
  currentTrackId,
  isPlaying,
  downloads = {},
  onPlay,
  onToggleLike,
  onDownload,
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
}: SearchViewProps) => {
  const [query, setQuery] = useState<string>(initialQuery);
  const [filterType, setFilterType] = useState<string>('songs');
  const [results, setResults] = useState<Track[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const debounceTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (initialQuery !== undefined) {
      setQuery(initialQuery);
    }
  }, [initialQuery]);

  const executeSearch = useCallback(async (q: string, filter: string) => {
    const trimmed = q.trim();
    if (!trimmed) {
      setResults([]);
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const data = await bridgeApi.search(trimmed, filter === 'all' ? 'songs' : filter, 30);
      setResults(data);
    } catch {
      setResults([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Debounce search query by 250ms per UI_SPEC.md
  useEffect(() => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = window.setTimeout(() => {
      executeSearch(query, filterType);
    }, 250);

    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [query, filterType, executeSearch]);

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden select-none">
      {/* Search Header & Filter Chips */}
      <div className="flex-none p-4 border-b border-border-subtle bg-bg-surface flex flex-col gap-3">
        <div className="flex items-center gap-2 px-3 py-2 bg-bg-base border border-border-subtle rounded-lg focus-within:border-accent transition-colors">
          <SearchIcon size={16} className="text-text-2 flex-none" />
          <input
            type="text"
            placeholder="Search tracks, albums, artists..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full bg-transparent text-sm text-text-1 placeholder-text-2 focus:outline-none"
            autoFocus
          />
          {isLoading && <Loader2 size={16} className="text-accent animate-spin flex-none" />}
        </div>

        {/* Filter Chips */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
          {FILTER_TYPES.map((chip) => (
            <button
              key={chip.id}
              onClick={() => setFilterType(chip.id)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                filterType === chip.id
                  ? 'bg-accent text-bg-base font-semibold'
                  : 'bg-bg-hover text-text-2 hover:text-text-1 border border-border-subtle'
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>

      {/* Results TrackList */}
      <TrackList
        tracks={results}
        currentTrackId={currentTrackId}
        isPlaying={isPlaying}
        downloads={downloads}
        onPlay={(track, index) => onPlay(track, index, results)}
        onToggleLike={onToggleLike}
        onDownload={onDownload}
        onAddNext={onAddNext}
        onAppendQueue={onAppendQueue}
        onOpenAddToPlaylist={onOpenAddToPlaylist}
        emptyMessage={
          query.trim()
            ? 'No matches found. Try another search query.'
            : 'Type above to search songs, albums, and playlists on YouTube Music.'
        }
      />
    </div>
  );
};
