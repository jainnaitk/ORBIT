import { memo, useEffect, useState, useCallback, useRef, useMemo } from 'react';
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
} from 'lucide-react';
import { TrackList } from '../components/TrackList';
import { TrackImage } from '../components/TrackImage';
import type { Track, Playlist, DownloadProgress } from '../api/types';
import { bridgeApi } from '../api/client';
import type { NavTab } from '../components/Sidebar';

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
}

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
}: LibraryViewProps) => {
  const [fetchedTracks, setFetchedTracks] = useState<Track[]>([]);
  const [historySort, setHistorySort] = useState<'recent' | 'most_played'>('recent');
  const [isLoading, setIsLoading] = useState<boolean>(tab === 'history' || tab === 'playlist');
  const reqSeqRef = useRef<number>(0);
  const prevViewKeyRef = useRef<string | null>(null);

  const needsFetch = tab === 'history' || (tab === 'playlist' && !!playlistId);
  const viewKey = `${tab}:${playlistId || ''}:${historySort}`;

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
          const list = await bridgeApi.getHistory(100, historySort);
          if (mySeq !== reqSeqRef.current) return;
          setFetchedTracks(list);
        } else if (tab === 'playlist' && playlistId) {
          const list = await bridgeApi.getPlaylistTracks(playlistId);
          if (mySeq !== reqSeqRef.current) return;
          setFetchedTracks(list);
        }
      } catch (err) {
        console.error('LibraryView loadData failed:', err);
      } finally {
        if (mySeq === reqSeqRef.current) {
          setIsLoading(false);
        }
      }
    },
    [needsFetch, tab, playlistId, historySort]
  );

  useEffect(() => {
    const viewChanged = prevViewKeyRef.current !== viewKey;
    prevViewKeyRef.current = viewKey;
    if (needsFetch && viewChanged) {
      setFetchedTracks([]);
      setIsLoading(true);
    }
    void loadData(needsFetch && viewChanged);
  }, [loadData, libraryVersion, needsFetch, viewKey]);

  const tracks: Track[] =
    tab === 'liked' || tab === 'library'
      ? likedTracks
      : tab === 'downloads'
      ? downloadTracks
      : fetchedTracks;

  const handleDeletePlaylist = async () => {
    if (!playlistId || !window.confirm('Delete this playlist?')) return;
    try {
      await bridgeApi.deletePlaylist(playlistId);
      onPlaylistDeleted(playlistId);
    } catch {
      // Ignore
    }
  };

  const totalDuration = tracks.reduce((acc, t) => acc + (t.duration_s || 0), 0);
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
        desc: `${tracks.length} tracks • ${formatTotalTime(totalDuration)}`,
        icon: <Heart size={28} className="fill-[#FF4D8D] text-[#FF4D8D]" />,
      };
    }
    if (tab === 'downloads') {
      return {
        badge: 'Offline Storage',
        title: 'Downloads',
        desc: `${tracks.length} offline tracks ready for playback`,
        icon: <FolderDown size={28} className="text-[#34D399]" />,
      };
    }
    if (tab === 'history') {
      return {
        badge: historySort === 'most_played' ? 'Top Replay' : 'Playback Log',
        title: historySort === 'most_played' ? 'Most Played' : 'Listening History',
        desc:
          isLoading && tracks.length === 0
            ? 'Loading listening log...'
            : `${tracks.length} tracks ${
                historySort === 'most_played' ? 'ranked by total plays' : 'recently played'
              }`,
        icon:
          historySort === 'most_played' ? (
            <TrendingUp size={28} className="text-[#34D399]" />
          ) : (
            <Clock size={28} style={{ color: 'var(--dynamic-accent)' }} />
          ),
      };
    }
    if (tab === 'playlist') {
      return {
        badge: 'Playlist',
        title: activePlaylist?.name || 'Playlist',
        desc:
          isLoading && tracks.length === 0
            ? 'Loading playlist...'
            : `${tracks.length} tracks • ${formatTotalTime(totalDuration)}`,
        icon: <ListMusic size={28} style={{ color: 'var(--dynamic-accent)' }} />,
      };
    }
    return {
      badge: 'Library',
      title: 'Your Library',
      desc: `${tracks.length} total saved tracks`,
      icon: <ListMusic size={28} style={{ color: 'var(--dynamic-accent)' }} />,
    };
  };

  const header = getHeaderInfo();

  return (
    <div
      key={`${tab}-${playlistId || ''}`}
      className="flex-1 flex flex-col h-full overflow-hidden select-none ios-liquid-panel eh-page-in rounded-[26px] my-1"
    >
      <div className="flex-none p-5 sm:p-6 border-b border-white/[0.08] bg-white/[0.02] space-y-4">
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-4 sm:gap-5 min-w-0 flex-1">
            <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-2xl bg-[#06080F] border border-white/10 flex items-center justify-center shadow-lg overflow-hidden flex-none">
              {tracks[0] ? (
                <TrackImage
                  src={tracks[0].thumbnail_url}
                  videoId={tracks[0].video_id}
                  size="md"
                  iconSize={28}
                  eager
                />
              ) : (
                header.icon
              )}
            </div>

            <div className="min-w-0 flex-1">
              <span
                className="text-[10px] uppercase tracking-[0.2em] font-semibold block"
                style={{ color: 'var(--dynamic-accent)' }}
              >
                {header.badge}
              </span>
              <h1 className="text-xl sm:text-2xl font-bold text-[#F1F4FA] mt-0.5 truncate">
                {header.title}
              </h1>
              <p className="text-xs text-[#9AA5C4] tabular-nums mt-1 truncate">{header.desc}</p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-none">
            {tracks.length > 0 && (
              <>
                <button
                  onClick={() => onPlayAll(tracks, false)}
                  className="px-4 py-2 bg-[#F4F6FF] text-[#020308] font-semibold text-xs rounded-full flex items-center gap-2 hover:scale-[1.02] active:scale-95 transition-transform shadow-md"
                >
                  <Play size={14} className="fill-current" />
                  Play All
                </button>
                <button
                  onClick={() => onPlayAll(tracks, true)}
                  className="px-3.5 py-2 ios-liquid-pill text-[#F4F6FF] font-medium text-xs rounded-full flex items-center gap-1.5"
                >
                  <Shuffle size={14} />
                  Shuffle
                </button>
                {tab !== 'downloads' && (
                  <button
                    onClick={() => onDownloadAll(tracks)}
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

        {tab === 'history' && (
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={() => setHistorySort('recent')}
              className={`px-3.5 py-1.5 rounded-full text-xs font-medium flex items-center gap-1.5 transition-all ${
                historySort === 'recent'
                  ? 'bg-[#F4F6FF] text-[#020308] font-semibold'
                  : 'ios-liquid-pill text-[#B4BEDB] hover:text-white'
              }`}
            >
              <Clock size={13} />
              <span>Recently Played</span>
            </button>
            <button
              onClick={() => setHistorySort('most_played')}
              className={`px-3.5 py-1.5 rounded-full text-xs font-medium flex items-center gap-1.5 transition-all ${
                historySort === 'most_played'
                  ? 'bg-[#F4F6FF] text-[#020308] font-semibold'
                  : 'ios-liquid-pill text-[#B4BEDB] hover:text-white'
              }`}
            >
              <TrendingUp size={13} />
              <span>Most Played</span>
            </button>
          </div>
        )}
      </div>

      <TrackList
        tracks={tracks}
        loading={needsFetch && isLoading}
        currentTrackId={currentTrackId}
        isPlaying={isPlaying}
        downloads={downloads}
        likedIds={likedIds}
        downloadedIds={downloadedIds}
        showPlayCount={tab === 'history'}
        isDownloadsTab={tab === 'downloads'}
        onPlay={(track, index) => onPlay(track, index, tracks)}
        onToggleLike={onToggleLike}
        onDownload={onDownload}
        onDeleteDownload={onDeleteDownload}
        onAddNext={onAddNext}
        onAppendQueue={onAppendQueue}
        onOpenAddToPlaylist={onOpenAddToPlaylist}
        emptyMessage={
          tab === 'liked'
            ? 'No liked tracks yet. Heart songs from Home, Search, or the Player dock.'
            : tab === 'downloads'
            ? 'No offline downloads yet. Click the download icon on any song.'
            : tab === 'history'
            ? 'No listening history yet. Play songs to build your Recent & Most Played charts.'
            : 'This playlist is empty.'
        }
      />
    </div>
  );
});
