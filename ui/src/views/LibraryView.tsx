import { memo, useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  Play,
  Shuffle,
  Download,
  Trash2,
  Heart,
  Clock,
  ListMusic,
  FolderDown,
  TrendingUp,
  Search,
  X,
  ArrowUpDown,
  MoreVertical,
  CheckCircle2,
  Loader2,
} from 'lucide-react';
import { TrackList } from '../components/TrackList';
import { TrackImage } from '../components/TrackImage';
import { ArtistLinks } from '../components/ArtistLinks';
import {
  TrackContextMenu,
  isTrackDownloaded,
  useTrackContextMenu,
} from '../components/TrackContextMenu';
import { cancelTrackPreload, scheduleTrackPreload, bridgeApi } from '../api/client';
import type { Track, Playlist, DownloadProgress } from '../api/types';
import type { NavTab } from '../components/Sidebar';

type SortField = 'added' | 'title' | 'artist';
type HistoryFlatRow =
  | { type: 'header'; id: string; label: string; count: number }
  | { type: 'item'; track: Track; flatIndex: number };

interface LibraryViewProps {
  tab: NavTab;
  playlistId?: string | null;
  playlists: Playlist[];
  likedTracks: Track[];
  downloadTracks: Track[];
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  libraryVersion?: number;
  onPlay: (track: Track, index: number, allTracks: Track[]) => void;
  onPlayAll: (tracks: Track[], shuffle?: boolean) => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onDeleteDownload: (track: Track) => void;
  onDownloadAll: (tracks: Track[]) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist: (track: Track) => void;
  onPlaylistDeleted: (playlistId: string) => void;
  onOpenArtist?: (artistName: string, artistId?: string | null) => void;
  onOpenAlbum?: (albumName: string, albumId: string | null | undefined, track: Track) => void;
}

const __ORBIT_LIB_CACHE__: Record<string, Track[]> = {};
const MAX_LIB_CACHE_KEYS = 12;

function setLibCache(key: string, items: Track[]): void {
  const keys = Object.keys(__ORBIT_LIB_CACHE__);
  if (keys.length >= MAX_LIB_CACHE_KEYS && !(key in __ORBIT_LIB_CACHE__)) {
    delete __ORBIT_LIB_CACHE__[keys[0]];
  }
  __ORBIT_LIB_CACHE__[key] = items;
}

function classifyHistoryBucket(iso?: string | null): 'Today' | 'This Week' | 'Earlier' {
  if (!iso) return 'Earlier';
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return 'Earlier';
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (parsed >= startOfToday) return 'Today';
  const startOfWeek = startOfToday - 6 * 86400_000;
  if (parsed >= startOfWeek) return 'This Week';
  return 'Earlier';
}

function formatTrackTime(secs?: number): string {
  if (!secs || Number.isNaN(secs)) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

const HISTORY_HEADER_H = 34;
const HISTORY_ROW_H = 62;

export const LibraryView = memo(({
  tab,
  playlistId,
  playlists,
  likedTracks,
  downloadTracks,
  currentTrackId,
  isPlaying,
  downloads = {},
  likedIds,
  downloadedIds,
  libraryVersion = 0,
  onPlay,
  onPlayAll,
  onToggleLike,
  onDownload,
  onDeleteDownload,
  onDownloadAll,
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
  onPlaylistDeleted,
  onOpenArtist,
  onOpenAlbum,
}: LibraryViewProps) => {
  const [historySort, setHistorySort] = useState<'recent' | 'most_played'>('recent');
  const [filterText, setFilterText] = useState<string>('');
  const [sortBy, setSortBy] = useState<SortField>('added');
  const [sortAsc, setSortAsc] = useState<boolean>(false);

  const needsFetch = tab === 'history' || (tab === 'playlist' && !!playlistId);
  const viewKey = `${tab}:${playlistId || ''}:${historySort}`;
  const cachedForKey = needsFetch ? __ORBIT_LIB_CACHE__[viewKey] : undefined;

  const [fetchedState, setFetchedState] = useState<{ key: string; items: Track[] }>(() => ({
    key: viewKey,
    items: cachedForKey ?? [],
  }));
  const [isLoading, setIsLoading] = useState<boolean>(needsFetch && !cachedForKey);
  const reqSeqRef = useRef<number>(0);
  const historyListRef = useRef<HTMLDivElement>(null);
  const { menuState, menuRef, openMenuAt, closeMenu } = useTrackContextMenu();

  useEffect(() => {
    setFilterText('');
    setSortBy('added');
    setSortAsc(false);
  }, [tab, playlistId]);

  const activePlaylist = useMemo(
    () => (tab === 'playlist' && playlistId ? playlists.find((p) => p.id === playlistId) || null : null),
    [tab, playlistId, playlists]
  );

  const loadData = useCallback(
    async (showLoader: boolean) => {
      if (!needsFetch) {
        setIsLoading(false);
        return;
      }
      const mySeq = ++reqSeqRef.current;
      if (showLoader) setIsLoading(true);
      try {
        if (tab === 'history') {
          const list = await bridgeApi.getHistory(150, historySort);
          if (mySeq !== reqSeqRef.current) return;
          setLibCache(viewKey, list);
          setFetchedState({ key: viewKey, items: list });
        } else if (tab === 'playlist' && playlistId) {
          const list = await bridgeApi.getPlaylistTracks(playlistId);
          if (mySeq !== reqSeqRef.current) return;
          setLibCache(viewKey, list);
          setFetchedState({ key: viewKey, items: list });
        }
      } catch (err) {
        console.error('LibraryView loadData failed:', err);
      } finally {
        if (mySeq === reqSeqRef.current) {
          setIsLoading(false);
        }
      }
    },
    [needsFetch, tab, playlistId, historySort, viewKey]
  );

  useEffect(() => {
    if (!needsFetch) {
      setIsLoading(false);
      return;
    }
    const cached = __ORBIT_LIB_CACHE__[viewKey];
    if (cached) {
      setFetchedState({ key: viewKey, items: cached });
      setIsLoading(false);
      void loadData(false);
    } else {
      setFetchedState({ key: viewKey, items: [] });
      setIsLoading(true);
      void loadData(true);
    }
  }, [loadData, libraryVersion, needsFetch, viewKey]);

  const fetchedTracks =
    fetchedState.key === viewKey ? fetchedState.items : (cachedForKey ?? []);

  const rawTracks: Track[] =
    tab === 'liked' || tab === 'library'
      ? likedTracks
      : tab === 'downloads'
      ? downloadTracks
      : fetchedTracks;

  const displayedTracks = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    let list = q
      ? rawTracks.filter(
          (t) =>
            t.title.toLowerCase().includes(q) ||
            t.artist.toLowerCase().includes(q) ||
            (t.album || '').toLowerCase().includes(q)
        )
      : rawTracks;

    if (tab !== 'history') {
      if (sortBy === 'added') {
        if (sortAsc) list = [...list].reverse();
      } else {
        const mult = sortAsc ? 1 : -1;
        list = [...list].sort((a, b) => {
          const av = (sortBy === 'title' ? a.title : a.artist) || '';
          const bv = (sortBy === 'title' ? b.title : b.artist) || '';
          return av.localeCompare(bv) * mult;
        });
      }
    }
    return list;
  }, [rawTracks, filterText, tab, sortBy, sortAsc]);

  const groupedHistoryRows = useMemo<HistoryFlatRow[]>(() => {
    if (tab !== 'history' || historySort !== 'recent') return [];
    const buckets: Record<'Today' | 'This Week' | 'Earlier', Track[]> = {
      Today: [],
      'This Week': [],
      Earlier: [],
    };
    displayedTracks.forEach((track) => {
      buckets[classifyHistoryBucket(track.last_played_at)].push(track);
    });
    const rows: HistoryFlatRow[] = [];
    let runningIdx = 0;
    (['Today', 'This Week', 'Earlier'] as const).forEach((label) => {
      const items = buckets[label];
      if (items.length === 0) return;
      rows.push({ type: 'header', id: `hist-${label}`, label, count: items.length });
      items.forEach((track) => {
        rows.push({ type: 'item', track, flatIndex: runningIdx++ });
      });
    });
    return rows;
  }, [tab, historySort, displayedTracks]);

  const historyVirtualizer = useVirtualizer({
    count: tab === 'history' && historySort === 'recent' ? groupedHistoryRows.length : 0,
    getScrollElement: () => historyListRef.current,
    estimateSize: (i) =>
      groupedHistoryRows[i]?.type === 'header' ? HISTORY_HEADER_H : HISTORY_ROW_H,
    overscan: 6,
  });

  const handleRemoveHistory = async (videoId: string) => {
    try {
      await bridgeApi.removeHistoryEntry(videoId);
      setFetchedState((prev) => ({
        ...prev,
        items: prev.items.filter((t) => t.video_id !== videoId),
      }));
      if (__ORBIT_LIB_CACHE__[viewKey]) {
        __ORBIT_LIB_CACHE__[viewKey] = __ORBIT_LIB_CACHE__[viewKey].filter(
          (t) => t.video_id !== videoId
        );
      }
    } catch (err) {
      console.error('Failed to remove history item:', err);
    }
  };

  const handleDeletePlaylist = async () => {
    if (!playlistId || !window.confirm('Delete this playlist?')) return;
    try {
      await bridgeApi.deletePlaylist(playlistId);
      onPlaylistDeleted(playlistId);
    } catch {
      // Ignore
    }
  };

  const totalDuration = rawTracks.reduce((acc, t) => acc + (t.duration_s || 0), 0);
  const formatTotalTime = (secs: number) => {
    const mins = Math.floor(secs / 60);
    const hrs = Math.floor(mins / 60);
    return hrs > 0 ? `${hrs} hr ${mins % 60} min` : `${mins} min`;
  };

  const getHeaderInfo = () => {
    if (tab === 'liked') {
      return {
        badge: 'Collection',
        title: 'Liked Songs',
        desc: `${rawTracks.length} tracks • ${formatTotalTime(totalDuration)}`,
        icon: <Heart size={36} className="fill-[#FF4D8D] text-[#FF4D8D]" />,
      };
    }
    if (tab === 'downloads') {
      return {
        badge: 'Offline Storage',
        title: 'Downloads',
        desc: `${rawTracks.length} offline tracks ready`,
        icon: <FolderDown size={36} className="text-[#34D399]" />,
      };
    }
    if (tab === 'history') {
      return {
        badge: historySort === 'most_played' ? 'Top Replay' : 'Playback Log',
        title: historySort === 'most_played' ? 'Most Played' : 'Listening History',
        desc:
          isLoading && rawTracks.length === 0
            ? 'Loading listening log...'
            : `${rawTracks.length} tracks logged`,
        icon:
          historySort === 'most_played' ? (
            <TrendingUp size={36} className="text-[#34D399]" />
          ) : (
            <Clock size={36} style={{ color: 'var(--dynamic-accent)' }} />
          ),
      };
    }
    if (tab === 'playlist') {
      return {
        badge: 'Playlist',
        title: activePlaylist?.name || 'Playlist',
        desc:
          isLoading && rawTracks.length === 0
            ? 'Loading playlist...'
            : `${rawTracks.length} tracks • ${formatTotalTime(totalDuration)}`,
        icon: <ListMusic size={36} style={{ color: 'var(--dynamic-accent)' }} />,
      };
    }
    return {
      badge: 'Library',
      title: 'Your Library',
      desc: `${rawTracks.length} total saved tracks`,
      icon: <ListMusic size={36} style={{ color: 'var(--dynamic-accent)' }} />,
    };
  };

  const header = getHeaderInfo();

  return (
    <div
      style={{ contain: 'layout style' }}
      className="flex-1 flex flex-col lg:flex-row h-full overflow-hidden select-none ios-liquid-panel rounded-[26px] my-1"
    >
      {/* Left Sticky Editorial Header Column */}
      <aside className="flex-none lg:w-[270px] xl:w-[295px] p-5 sm:p-6 border-b lg:border-b-0 lg:border-r border-white/[0.08] bg-white/[0.02] flex flex-col justify-between gap-5">
        <div className="flex lg:flex-col items-center lg:items-start gap-4 lg:gap-4 min-w-0">
          <div className="w-20 h-20 lg:w-44 lg:h-44 rounded-2xl bg-[#06080F] border border-white/10 flex items-center justify-center shadow-2xl overflow-hidden flex-none">
            {rawTracks[0] ? (
              <TrackImage
                src={rawTracks[0].thumbnail_url}
                videoId={rawTracks[0].video_id}
                size="md"
                iconSize={34}
                eager
              />
            ) : (
              header.icon
            )}
          </div>

          <div className="min-w-0 flex-1 w-full">
            <span
              className="text-[10px] uppercase tracking-[0.2em] font-semibold block"
              style={{ color: 'var(--dynamic-accent)' }}
            >
              {header.badge}
            </span>
            <h1 className="text-xl lg:text-2xl font-bold text-[#F1F4FA] mt-0.5 line-clamp-2">
              {header.title}
            </h1>
            <p className="text-xs text-[#9AA5C4] tabular-nums mt-1 truncate">{header.desc}</p>
          </div>
        </div>

        <div className="space-y-3">
          {tab === 'history' && (
            <div className="flex items-center gap-1 p-0.5 rounded-full bg-white/[0.05] border border-white/[0.09] w-fit">
              <button
                onClick={() => setHistorySort('recent')}
                className={`px-3 py-1 rounded-full text-[11px] font-medium flex items-center gap-1.5 transition-colors ${
                  historySort === 'recent'
                    ? 'bg-[#F4F6FF] text-[#020308] font-semibold'
                    : 'text-[#B4BEDB] hover:text-white'
                }`}
              >
                <Clock size={12} />
                <span>Recent</span>
              </button>
              <button
                onClick={() => setHistorySort('most_played')}
                className={`px-3 py-1 rounded-full text-[11px] font-medium flex items-center gap-1.5 transition-colors ${
                  historySort === 'most_played'
                    ? 'bg-[#F4F6FF] text-[#020308] font-semibold'
                    : 'text-[#B4BEDB] hover:text-white'
                }`}
              >
                <TrendingUp size={12} />
                <span>Most Played</span>
              </button>
            </div>
          )}

          <div className="flex items-center gap-2 flex-wrap">
            {displayedTracks.length > 0 && (
              <>
                <button
                  onClick={() => onPlayAll(displayedTracks, false)}
                  className="px-4 py-2 bg-[#F4F6FF] text-[#020308] font-semibold text-xs rounded-full flex items-center gap-2 hover:scale-[1.02] active:scale-95 transition-transform shadow-md"
                >
                  <Play size={14} className="fill-current" />
                  Play All
                </button>
                <button
                  onClick={() => onPlayAll(displayedTracks, true)}
                  className="px-3.5 py-2 ios-liquid-pill text-[#F4F6FF] font-medium text-xs rounded-full flex items-center gap-1.5"
                >
                  <Shuffle size={14} />
                  Shuffle
                </button>
                {tab !== 'downloads' && (
                  <button
                    onClick={() => onDownloadAll(displayedTracks)}
                    title="Download all tracks"
                    className="p-2 ios-liquid-pill text-[#A6B0CF] hover:text-[#34D399] rounded-full"
                  >
                    <Download size={14} />
                  </button>
                )}
              </>
            )}

            {tab === 'playlist' && (
              <button
                onClick={handleDeletePlaylist}
                title="Delete Playlist"
                className="p-2 ios-liquid-pill text-[#A6B0CF] hover:text-[#FF4D8D] rounded-full"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        </div>
      </aside>

      {/* Right Scrollable Track List Column */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Filter + Sort Bar */}
        <div className="flex-none px-4 sm:px-5 py-2.5 border-b border-white/[0.08] flex items-center justify-between gap-2.5 flex-wrap bg-white/[0.015]">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-black/40 border border-white/10 w-full sm:w-64">
            <Search size={12} className="text-[#8E99BA] flex-none" />
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder={`Filter ${header.title.toLowerCase()}...`}
              className="bg-transparent text-xs text-[#F4F6FF] placeholder-[#6D7796] focus:outline-none flex-1 min-w-0"
            />
            {filterText && (
              <button
                type="button"
                onClick={() => setFilterText('')}
                className="text-[#8E99BA] hover:text-white"
              >
                <X size={12} />
              </button>
            )}
          </div>

          {tab !== 'history' && (
            <div className="flex items-center gap-1.5">
              <select
                value={sortBy}
                onChange={(e) => {
                  const field = e.target.value as SortField;
                  setSortBy(field);
                  setSortAsc(field === 'added' ? false : true);
                }}
                className="h-8 px-2.5 rounded-full bg-white/[0.06] border border-white/[0.10] text-[11px] text-[#E6ECF8] focus:outline-none cursor-pointer"
              >
                <option value="added" className="bg-[#0B0F1A]">
                  Date Added
                </option>
                <option value="title" className="bg-[#0B0F1A]">
                  Title
                </option>
                <option value="artist" className="bg-[#0B0F1A]">
                  Artist
                </option>
              </select>
              <button
                type="button"
                onClick={() => setSortAsc((v) => !v)}
                title={sortAsc ? 'Ascending order' : 'Descending order'}
                className="h-8 px-2.5 rounded-full bg-white/[0.06] border border-white/[0.10] text-[11px] text-[#A6B0CF] hover:text-white inline-flex items-center gap-1"
              >
                <ArrowUpDown size={12} />
                <span>{sortAsc ? 'Asc' : 'Desc'}</span>
              </button>
            </div>
          )}
        </div>

        {/* Virtualized Grouped History vs Standard TrackList */}
        {tab === 'history' && historySort === 'recent' ? (
          isLoading && displayedTracks.length === 0 ? (
            <div className="flex-1 p-5 space-y-2.5">
              {Array.from({ length: 8 }).map((_, i) => (
                <div
                  key={i}
                  className="h-14 rounded-xl bg-white/[0.04] border border-white/[0.06] eh-skeleton"
                />
              ))}
            </div>
          ) : groupedHistoryRows.length === 0 ? (
            <div className="flex-1 flex items-center justify-center p-8 text-xs text-[#8E99BA] text-center">
              {filterText
                ? 'No matching history tracks.'
                : 'No listening history yet. Play songs to build your Recent & Most Played charts.'}
            </div>
          ) : (
            <div
              ref={historyListRef}
              style={{ contain: 'strict', willChange: 'scroll-position' }}
              className="flex-1 overflow-y-auto px-3 sm:px-4 py-2 relative"
            >
              <div
                style={{
                  height: `${historyVirtualizer.getTotalSize()}px`,
                  width: '100%',
                  position: 'relative',
                }}
              >
                {historyVirtualizer.getVirtualItems().map((vRow) => {
                  const row = groupedHistoryRows[vRow.index];
                  if (!row) return null;

                  if (row.type === 'header') {
                    return (
                      <div
                        key={row.id}
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          height: `${HISTORY_HEADER_H}px`,
                          transform: `translate3d(0, ${vRow.start}px, 0)`,
                        }}
                        className="flex items-center justify-between px-3 text-[10px] font-bold uppercase tracking-[0.18em] text-[#8E99BA]"
                      >
                        <span>{row.label}</span>
                        <span className="font-mono tabular-nums text-[10px] text-[#6D7796]">
                          {row.count}
                        </span>
                      </div>
                    );
                  }

                  const track = row.track;
                  const isCurrent = currentTrackId === track.video_id;
                  const isLiked = likedIds ? likedIds.has(track.video_id) : !!track.is_liked;
                  const dl = downloads[track.video_id];
                  const isDownloaded = isTrackDownloaded(track, downloadedIds, downloads);
                  const isDownloading =
                    dl?.status === 'downloading' ||
                    dl?.status === 'queued' ||
                    dl?.status === 'tagging';

                  return (
                    <div
                      key={`${track.video_id}-${row.flatIndex}`}
                      onClick={() => onPlay(track, row.flatIndex, displayedTracks)}
                      onMouseEnter={() => scheduleTrackPreload(track)}
                      onMouseLeave={() => cancelTrackPreload(track.video_id)}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        openMenuAt(track, e.clientX, e.clientY, isDownloaded);
                      }}
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: '100%',
                        height: `${HISTORY_ROW_H}px`,
                        transform: `translate3d(0, ${vRow.start}px, 0)`,
                      }}
                      className={`flex items-center px-3 gap-3 rounded-xl hover:bg-white/[0.06] group cursor-pointer transition-colors ${
                        isCurrent ? 'bg-white/[0.08]' : ''
                      }`}
                    >
                      <div className="w-10 h-10 rounded-xl bg-black/70 border border-white/15 overflow-hidden flex-none">
                        <TrackImage
                          src={track.thumbnail_url}
                          videoId={track.video_id}
                          size="sm"
                          iconSize={16}
                        />
                      </div>

                      <div className="min-w-0 flex-1">
                        <div
                          className="text-xs font-semibold truncate"
                          style={
                            isCurrent ? { color: 'var(--dynamic-accent)' } : { color: '#F4F6FF' }
                          }
                        >
                          {track.title}
                        </div>
                        <ArtistLinks
                          artist={track.artist}
                          artistId={track.artist_id}
                          artists={track.artists}
                          onOpenArtist={onOpenArtist}
                          className="text-[11px] text-[#9AA5C4]"
                        />
                      </div>

                      {(track.play_count ?? 0) > 1 && (
                        <span className="hidden md:inline-flex text-[10px] font-mono tabular-nums px-2 py-0.5 rounded-full bg-white/[0.06] text-[#9AA5C4]">
                          {track.play_count}x
                        </span>
                      )}

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleLike(track);
                        }}
                        className="p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] flex-none"
                      >
                        <Heart
                          size={14}
                          className={
                            isLiked ? 'fill-[#FF4D8D] text-[#FF4D8D]' : 'opacity-60 group-hover:opacity-100'
                          }
                        />
                      </button>

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (isDownloaded) onDeleteDownload(track);
                          else if (!isDownloading) onDownload(track);
                        }}
                        className="hidden sm:inline-flex p-1.5 text-[#A6B0CF] hover:text-[#34D399] flex-none"
                      >
                        {isDownloaded ? (
                          <CheckCircle2 size={14} className="text-[#34D399]" />
                        ) : isDownloading ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Download size={14} className="opacity-0 group-hover:opacity-80" />
                        )}
                      </button>

                      <button
                        type="button"
                        title="Remove from History"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleRemoveHistory(track.video_id);
                        }}
                        className="p-1.5 text-[#7A84A6] hover:text-[#FF6B9E] opacity-0 group-hover:opacity-100 transition-opacity flex-none"
                      >
                        <X size={13} />
                      </button>

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          openMenuAt(track, e.clientX, e.clientY, isDownloaded);
                        }}
                        className="p-1 text-[#8E99BA] hover:text-white opacity-0 group-hover:opacity-100 flex-none"
                      >
                        <MoreVertical size={14} />
                      </button>

                      <span className="hidden sm:block w-10 text-right text-[11px] font-mono tabular-nums text-[#7A84A6] flex-none">
                        {formatTrackTime(track.duration_s)}
                      </span>
                    </div>
                  );
                })}
              </div>

              <TrackContextMenu
                menuState={menuState}
                menuRef={menuRef}
                onClose={closeMenu}
                onAddNext={onAddNext}
                onAppendQueue={onAppendQueue}
                onOpenArtist={onOpenArtist}
                onOpenAlbum={onOpenAlbum}
                onOpenAddToPlaylist={onOpenAddToPlaylist}
                onDownload={onDownload}
                onDeleteDownload={onDeleteDownload}
              />
            </div>
          )
        ) : (
          <TrackList
            tracks={displayedTracks}
            loading={needsFetch && isLoading}
            scrollKey={`${viewKey}:${filterText}:${sortBy}:${sortAsc}`}
            currentTrackId={currentTrackId}
            isPlaying={isPlaying}
            downloads={downloads}
            likedIds={likedIds}
            downloadedIds={downloadedIds}
            showPlayCount={tab === 'history'}
            isDownloadsTab={tab === 'downloads'}
            onPlay={(track, index) => onPlay(track, index, displayedTracks)}
            onToggleLike={onToggleLike}
            onDownload={onDownload}
            onDeleteDownload={onDeleteDownload}
            onAddNext={onAddNext}
            onAppendQueue={onAppendQueue}
            onOpenAddToPlaylist={onOpenAddToPlaylist}
            onOpenArtist={onOpenArtist}
            onOpenAlbum={onOpenAlbum}
            emptyMessage={
              filterText
                ? 'No tracks match your filter.'
                : tab === 'liked'
                ? 'No liked tracks yet. Heart songs from Home, Search, or the Player dock.'
                : tab === 'downloads'
                ? 'No offline downloads yet. Click the download icon on any song.'
                : tab === 'history'
                ? 'No listening history yet. Play songs to build your Recent & Most Played charts.'
                : 'This playlist is empty.'
            }
          />
        )}
      </div>
    </div>
  );
});
