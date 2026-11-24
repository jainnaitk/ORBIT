import { memo, useEffect, useState, useRef } from 'react';
import { Play, Shuffle, Download, Disc3 } from 'lucide-react';
import { TrackImage } from '../components/TrackImage';
import { TrackList } from '../components/TrackList';
import { bridgeApi } from '../api/client';
import { useTwoFingerClose } from '../utils/gestures';
import type { AlbumDetails, DownloadProgress, Track } from '../api/types';
import type { AlbumNavTarget, ArtistNavTarget } from './ArtistView';

interface AlbumViewProps {
  target: AlbumNavTarget;
  currentTrackId?: string | null;
  isPlaying?: boolean;
  downloads?: Record<string, DownloadProgress>;
  likedIds?: Set<string>;
  downloadedIds?: Set<string>;
  onPlay: (track: Track, index: number, allTracks: Track[]) => void;
  onPlayAll: (tracks: Track[], shuffle?: boolean) => void;
  onToggleLike: (track: Track) => void;
  onDownload: (track: Track) => void;
  onDeleteDownload: (track: Track) => void;
  onDownloadAll: (tracks: Track[]) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist: (track: Track) => void;
  onOpenArtist: (artist: ArtistNavTarget) => void;
  onClose?: () => void;
}

const albumCache = new Map<string, AlbumDetails>();
const MAX_ALBUM_CACHE = 6;

function formatTotalTime(secs: number): string {
  const mins = Math.floor(secs / 60);
  const hrs = Math.floor(mins / 60);
  return hrs > 0 ? `${hrs} hr ${mins % 60} min` : `${mins} min`;
}

export const AlbumView = memo(({
  target,
  currentTrackId,
  isPlaying,
  downloads = {},
  likedIds,
  downloadedIds,
  onPlay,
  onPlayAll,
  onToggleLike,
  onDownload,
  onDeleteDownload,
  onDownloadAll,
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
  onOpenArtist,
  onClose,
}: AlbumViewProps) => {
  const cacheKey = `${(target.browseId || '').trim()}::${(target.title || '').trim().toLowerCase()}::${(target.artist || '').trim().toLowerCase()}::${(target.videoId || '').trim()}`;
  const containerRef = useRef<HTMLDivElement>(null);
  const [album, setAlbum] = useState<AlbumDetails | null>(
    () => albumCache.get(cacheKey) || null
  );
  const [loading, setLoading] = useState<boolean>(!albumCache.has(cacheKey));
  const [error, setError] = useState<string | null>(null);
  const reqSeqRef = useRef<number>(0);

  useEffect(() => {
    setError(null);
    const cached = albumCache.get(cacheKey);
    if (cached) {
      setAlbum(cached);
      setLoading(false);
      return;
    }

    const mySeq = ++reqSeqRef.current;
    setAlbum(null);
    setLoading(true);

    bridgeApi
      .getAlbumDetails({
        browseId: target.browseId || undefined,
        title: target.title || undefined,
        artist: target.artist || undefined,
        videoId: target.videoId || undefined,
      })
      .then((data) => {
        if (mySeq !== reqSeqRef.current) return;
        if (albumCache.size >= MAX_ALBUM_CACHE && !albumCache.has(cacheKey)) {
          const oldest = albumCache.keys().next().value;
          if (oldest) albumCache.delete(oldest);
        }
        albumCache.set(cacheKey, data);
        setAlbum(data);
      })
      .catch((err: any) => {
        if (mySeq !== reqSeqRef.current) return;
        setError(err?.message || 'Unable to load album details');
      })
      .finally(() => {
        if (mySeq === reqSeqRef.current) setLoading(false);
      });
  }, [cacheKey, target.browseId, target.title, target.artist, target.videoId]);

  const tracks = album?.tracks || [];
  const totalDuration = tracks.reduce((acc, t) => acc + (t.duration_s || 0), 0);
  const displayArtist = album?.artist || target.artist || 'Unknown Artist';

  useTwoFingerClose({
    enabled: Boolean(onClose),
    ready: !loading,
    settleMs: 500,
    containerRef,
    onClose: () => onClose?.(),
  });

  return (
    <div
      ref={containerRef}
      style={{ contain: 'layout style' }}
      className="flex-1 flex flex-col h-full overflow-hidden select-none ios-liquid-panel rounded-[26px] my-1"
    >
      {/* Album Header */}
      <div className="flex-none p-5 sm:p-6 border-b border-white/[0.08] bg-gradient-to-br from-[rgba(var(--dynamic-accent-rgb),0.12)] via-white/[0.02] to-transparent">
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-4 sm:gap-5 min-w-0 flex-1">
            <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-2xl bg-[#06080F] border border-white/15 flex items-center justify-center shadow-xl overflow-hidden flex-none">
              {album?.thumbnail_url || tracks[0]?.thumbnail_url ? (
                <TrackImage
                  src={album?.thumbnail_url || tracks[0]?.thumbnail_url}
                  videoId={tracks[0]?.video_id || target.videoId}
                  size="lg"
                  iconSize={32}
                  eager
                />
              ) : (
                <Disc3 size={34} style={{ color: 'var(--dynamic-accent)' }} />
              )}
            </div>

            <div className="min-w-0 flex-1">
              <span
                className="text-[10px] uppercase tracking-[0.22em] font-bold block"
                style={{ color: 'var(--dynamic-accent)' }}
              >
                {album?.type || 'Album'}
              </span>
              <h1 className="text-xl sm:text-2xl font-bold text-[#F4F6FF] mt-0.5 truncate">
                {album?.title || target.title || 'Album'}
              </h1>
              <div className="flex items-center gap-2 flex-wrap text-xs text-[#9AA5C4] mt-1">
                <button
                  type="button"
                  onClick={() =>
                    onOpenArtist({
                      name: displayArtist.split(',')[0].trim(),
                      channelId: album?.artist_id,
                    })
                  }
                  className="font-semibold text-[#E6ECF8] hover:underline hover:text-white"
                >
                  {displayArtist}
                </button>
                {album?.year && <span>• {album.year}</span>}
                {!loading && tracks.length > 0 && (
                  <span className="tabular-nums">
                    • {tracks.length} {tracks.length === 1 ? 'track' : 'tracks'} •{' '}
                    {formatTotalTime(totalDuration)}
                  </span>
                )}
              </div>
            </div>
          </div>

          {tracks.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap flex-none">
              <button
                type="button"
                onClick={() => onPlayAll(tracks, false)}
                className="px-4 py-2 bg-[#F4F6FF] text-[#020308] font-semibold text-xs rounded-full flex items-center gap-2 hover:scale-[1.02] active:scale-95 transition-transform shadow-md"
              >
                <Play size={14} className="fill-current" />
                Play Album
              </button>
              <button
                type="button"
                onClick={() => onPlayAll(tracks, true)}
                className="px-3.5 py-2 ios-liquid-pill text-[#F4F6FF] font-medium text-xs rounded-full flex items-center gap-1.5"
              >
                <Shuffle size={14} />
                Shuffle
              </button>
              <button
                type="button"
                onClick={() => onDownloadAll(tracks)}
                title="Download all tracks in album"
                className="p-2 ios-liquid-pill text-[#A6B0CF] hover:text-[#34D399] rounded-full"
              >
                <Download size={14} />
              </button>
            </div>
          )}
        </div>
      </div>

      {error && tracks.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-[#A6B0CF] gap-2">
          <Disc3 size={32} className="opacity-35" />
          <p className="text-sm font-semibold text-[#F4F6FF]">Could not load album</p>
          <p className="text-xs text-[#7A84A6]">{error}</p>
        </div>
      ) : (
        <TrackList
          tracks={tracks}
          loading={loading}
          scrollKey={cacheKey}
          currentTrackId={currentTrackId}
          isPlaying={isPlaying}
          downloads={downloads}
          likedIds={likedIds}
          downloadedIds={downloadedIds}
          onPlay={(track, index) => onPlay(track, index, tracks)}
          onToggleLike={onToggleLike}
          onDownload={onDownload}
          onDeleteDownload={onDeleteDownload}
          onAddNext={onAddNext}
          onAppendQueue={onAppendQueue}
          onOpenAddToPlaylist={onOpenAddToPlaylist}
          onOpenArtist={(artistName, artistId) =>
            onOpenArtist({ name: artistName, channelId: artistId })
          }
          emptyMessage="No tracks found for this release."
        />
      )}
    </div>
  );
});
