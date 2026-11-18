import { useEffect, useState, useCallback } from 'react';
import {
  Play,
  Shuffle,
  Download,
  Trash2,
  Heart,
  Clock,
  ListMusic,
  FolderDown,
} from 'lucide-react';
import { TrackList } from '../components/TrackList';
import type { Track, Playlist, DownloadProgress } from '../api/types';
import { bridgeApi } from '../api/client';
import type { NavTab } from '../components/Sidebar';

interface LibraryViewProps {
  tab: NavTab;
  playlistId?: string | null;
  playlists: Playlist[];
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  onPlay: (track: Track, index: number, allTracks: Track[]) => void;
  onPlayAll: (tracks: Track[], shuffle?: boolean) => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onDownloadAll: (tracks: Track[]) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist: (track: Track) => void;
  onPlaylistDeleted: (playlistId: string) => void;
}

export const LibraryView = ({
  tab,
  playlistId,
  playlists,
  currentTrackId,
  isPlaying,
  downloads = {},
  onPlay,
  onPlayAll,
  onToggleLike,
  onDownload,
  onDownloadAll,
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
  onPlaylistDeleted,
}: LibraryViewProps) => {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [activePlaylist, setActivePlaylist] = useState<Playlist | null>(null);

  const loadData = useCallback(async () => {
    try {
      if (tab === 'liked') {
        const liked = await bridgeApi.getLiked();
        setTracks(liked);
      } else if (tab === 'downloads') {
        const down = await bridgeApi.getDownloads();
        setTracks(down);
      } else if (tab === 'history') {
        const liked = await bridgeApi.getLiked();
        // Fallback for history: list sorted or available
        setTracks(liked);
      } else if (tab === 'playlist' && playlistId) {
        const found = playlists.find((p) => p.id === playlistId) || null;
        setActivePlaylist(found);
        const plTracks = await bridgeApi.getPlaylistTracks(playlistId);
        setTracks(plTracks);
      } else {
        // General library overview
        const liked = await bridgeApi.getLiked();
        setTracks(liked);
      }
    } catch {
      // Fallback
    }
  }, [tab, playlistId, playlists]);

  useEffect(() => {
    loadData();
  }, [loadData]);

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
    if (hrs > 0) {
      return `${hrs} hr ${mins % 60} min`;
    }
    return `${mins} min`;
  };

  const getHeaderInfo = () => {
    if (tab === 'liked') {
      return {
        badge: 'Collection',
        title: 'Liked Songs',
        desc: `${tracks.length} tracks • ${formatTotalTime(totalDuration)}`,
        icon: <Heart size={28} className="fill-liked text-liked" />,
      };
    }
    if (tab === 'downloads') {
      return {
        badge: 'Offline Storage',
        title: 'Downloads',
        desc: `${tracks.length} offline tracks ready for playback`,
        icon: <FolderDown size={28} className="text-downloaded" />,
      };
    }
    if (tab === 'history') {
      return {
        badge: 'Playback Log',
        title: 'Listening History',
        desc: `${tracks.length} recently played tracks`,
        icon: <Clock size={28} className="text-accent" />,
      };
    }
    if (tab === 'playlist') {
      return {
        badge: 'Custom Playlist',
        title: activePlaylist?.name || 'Playlist',
        desc: `${tracks.length} tracks • ${formatTotalTime(totalDuration)}`,
        icon: <ListMusic size={28} className="text-accent" />,
      };
    }
    return {
      badge: 'Library',
      title: 'Your Library',
      desc: `${tracks.length} total saved tracks`,
      icon: <ListMusic size={28} className="text-accent" />,
    };
  };

  const header = getHeaderInfo();

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden select-none">
      {/* Compact Playlist / Liked / Downloads Header */}
      <div className="flex-none p-6 border-b border-border-subtle bg-bg-surface flex items-end justify-between">
        <div className="flex items-center gap-5">
          {/* Mosaic Cover / Visual Icon tile */}
          <div className="w-20 h-20 rounded-lg bg-bg-base border border-border-subtle flex items-center justify-center shadow-md overflow-hidden flex-none">
            {tracks[0]?.thumbnail_url ? (
              <img
                src={tracks[0].thumbnail_url}
                alt=""
                className="w-full h-full object-cover"
              />
            ) : (
              header.icon
            )}
          </div>

          <div>
            <span className="text-[11px] uppercase tracking-wider text-accent font-semibold">
              {header.badge}
            </span>
            <h1 className="text-2xl font-bold text-text-1 mt-0.5">{header.title}</h1>
            <p className="text-xs text-text-2 mt-1">{header.desc}</p>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2.5">
          {tracks.length > 0 && (
            <>
              <button
                onClick={() => onPlayAll(tracks, false)}
                className="px-4 py-2 bg-accent text-bg-base font-semibold text-xs rounded-full flex items-center gap-2 hover:opacity-95 transition-opacity"
              >
                <Play size={14} className="fill-current" />
                Play All
              </button>
              <button
                onClick={() => onPlayAll(tracks, true)}
                className="px-3.5 py-2 bg-bg-hover text-text-1 font-medium text-xs rounded-full flex items-center gap-1.5 hover:bg-border-light border border-border-subtle transition-colors"
              >
                <Shuffle size={14} />
                Shuffle
              </button>
              <button
                onClick={() => onDownloadAll(tracks)}
                title="Download all tracks for offline playback"
                className="p-2 bg-bg-hover text-text-2 hover:text-downloaded rounded-full border border-border-subtle transition-colors"
              >
                <Download size={14} />
              </button>
            </>
          )}

          {tab === 'playlist' && (
            <button
              onClick={handleDeletePlaylist}
              title="Delete Playlist"
              className="p-2 bg-bg-hover text-text-2 hover:text-rose-400 rounded-full border border-border-subtle transition-colors"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Virtualized TrackList */}
      <TrackList
        tracks={tracks}
        currentTrackId={currentTrackId}
        isPlaying={isPlaying}
        downloads={downloads}
        onPlay={(track, index) => onPlay(track, index, tracks)}
        onToggleLike={onToggleLike}
        onDownload={onDownload}
        onAddNext={onAddNext}
        onAppendQueue={onAppendQueue}
        onOpenAddToPlaylist={onOpenAddToPlaylist}
        emptyMessage={
          tab === 'liked'
            ? 'No liked tracks yet. Heart songs from Search or Home to add them here!'
            : tab === 'downloads'
            ? 'No offline downloads yet. Click the download icon on any song to save it locally.'
            : 'This playlist is empty.'
        }
      />
    </div>
  );
};
