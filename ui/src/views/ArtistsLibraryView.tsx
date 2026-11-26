import { memo, useEffect, useMemo, useState } from 'react';
import {
  Play,
  Radio,
  Search,
  UserCheck,
  UserPlus,
  UserRound,
  UsersRound,
  X,
} from 'lucide-react';
import { TrackImage } from '../components/TrackImage';
import { bridgeApi } from '../api/client';
import type { ArtistsLibraryResponse, LibraryArtistSummary } from '../api/types';

interface ArtistsLibraryViewProps {
  libraryVersion?: number;
  onOpenArtist: (name: string, channelId?: string | null) => void;
  onArtistChanged?: () => void;
}

export const ArtistsLibraryView = memo(({
  libraryVersion = 0,
  onOpenArtist,
  onArtistChanged,
}: ArtistsLibraryViewProps) => {
  const [data, setData] = useState<ArtistsLibraryResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [filterText, setFilterText] = useState<string>('');
  const [playingArtistKey, setPlayingArtistKey] = useState<string | null>(null);

  const loadLibrary = () => {
    bridgeApi
      .getArtistsLibrary()
      .then((res) => setData(res))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setLoading((prev) => prev && data === null);
    loadLibrary();
  }, [libraryVersion]);

  const query = filterText.trim().toLowerCase();

  const followed = useMemo(() => {
    const items = data?.followed || [];
    if (!query) return items;
    return items.filter((a) => a.name.toLowerCase().includes(query));
  }, [data?.followed, query]);

  const inLibrary = useMemo(() => {
    const items = data?.in_library || [];
    if (!query) return items;
    return items.filter((a) => a.name.toLowerCase().includes(query));
  }, [data?.in_library, query]);

  const handleToggleFollow = async (e: React.MouseEvent, artist: LibraryArtistSummary) => {
    e.stopPropagation();
    try {
      if (artist.is_followed) {
        await bridgeApi.unfollowArtist(artist.channel_id || artist.name);
      } else {
        await bridgeApi.followArtist({
          channel_id: artist.channel_id,
          name: artist.name,
          thumbnail_url: artist.thumbnail_url,
        });
      }
      loadLibrary();
      onArtistChanged?.();
    } catch {
      // Ignore
    }
  };

  const handleStartRadio = async (e: React.MouseEvent, artist: LibraryArtistSummary) => {
    e.stopPropagation();
    setPlayingArtistKey(artist.artist_norm);
    try {
      await bridgeApi.startArtistRadio({
        channel_id: artist.channel_id,
        name: artist.name,
      });
    } finally {
      setPlayingArtistKey(null);
    }
  };

  const renderArtistCard = (artist: LibraryArtistSummary) => (
    <div
      key={`${artist.channel_id}-${artist.artist_norm}`}
      onClick={() => onOpenArtist(artist.name, artist.channel_id)}
      className="group ios-liquid-card rounded-2xl p-3.5 flex flex-col items-center text-center cursor-pointer relative"
    >
      <div className="relative w-24 h-24 rounded-full overflow-hidden bg-[#06080F] border border-white/15 flex items-center justify-center shadow-lg">
        {artist.thumbnail_url ? (
          <TrackImage
            src={artist.thumbnail_url}
            size="md"
            iconSize={28}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <UserRound size={30} className="text-[#8E99BA]" />
        )}
        <button
          type="button"
          title={`Play ${artist.name} Radio`}
          onClick={(e) => void handleStartRadio(e, artist)}
          className="absolute inset-0 bg-black/55 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity"
        >
          {playingArtistKey === artist.artist_norm ? (
            <Radio size={20} className="animate-pulse text-white" />
          ) : (
            <Play size={20} className="fill-white text-white ml-0.5" />
          )}
        </button>
      </div>

      <div className="mt-3 w-full">
        <div className="text-xs font-semibold text-[#F4F6FF] truncate">{artist.name}</div>
        <div className="text-[10.5px] text-[#8E99BA] truncate mt-0.5">
          {artist.track_count > 0
            ? `${artist.track_count} track${artist.track_count === 1 ? '' : 's'}${
                artist.offline_count ? ` • ${artist.offline_count} offline` : ''
              }`
            : 'Followed Artist'}
        </div>
      </div>

      <button
        type="button"
        title={artist.is_followed ? 'Unfollow artist' : 'Follow artist'}
        onClick={(e) => void handleToggleFollow(e, artist)}
        className={`mt-2.5 inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10.5px] font-medium border transition-colors ${
          artist.is_followed
            ? 'bg-white/[0.12] border-white/20 text-[#34D399]'
            : 'bg-white/[0.05] border-white/[0.10] text-[#B4BEDB] hover:text-white hover:bg-white/[0.12]'
        }`}
      >
        {artist.is_followed ? <UserCheck size={11} /> : <UserPlus size={11} />}
        <span>{artist.is_followed ? 'Following' : 'Follow'}</span>
      </button>
    </div>
  );

  return (
    <div
      style={{ contain: 'layout style' }}
      className="flex-1 flex flex-col h-full overflow-hidden select-none ios-liquid-panel rounded-[26px] my-1"
    >
      <div className="flex-none p-5 sm:p-6 border-b border-white/[0.08] bg-white/[0.02] flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3.5 min-w-0">
          <div className="w-12 h-12 rounded-2xl bg-white/[0.06] border border-white/[0.10] flex items-center justify-center flex-none">
            <UsersRound size={22} style={{ color: 'var(--dynamic-accent)' }} />
          </div>
          <div className="min-w-0">
            <span
              className="text-[10px] uppercase tracking-[0.2em] font-semibold block"
              style={{ color: 'var(--dynamic-accent)' }}
            >
              Personal Hub
            </span>
            <h1 className="text-xl sm:text-2xl font-bold text-[#F1F4FA] truncate">Artists</h1>
            <p className="text-xs text-[#9AA5C4] mt-0.5">
              {followed.length} followed • {inLibrary.length} in library
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-black/40 border border-white/12 w-full sm:w-64">
          <Search size={12} className="text-[#8E99BA] flex-none" />
          <input
            type="text"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            placeholder="Filter artists..."
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
      </div>

      <div className="flex-1 overflow-y-auto p-5 sm:p-6 space-y-7">
        {loading && !data ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3.5 eh-skeleton">
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="h-44 rounded-2xl ios-liquid-card" />
            ))}
          </div>
        ) : followed.length === 0 && inLibrary.length === 0 ? (
          <div className="py-16 text-center space-y-2 text-[#8E99BA]">
            <UsersRound size={32} className="mx-auto opacity-35" />
            <p className="text-sm font-semibold text-[#F4F6FF]">No artists yet</p>
            <p className="text-xs max-w-sm mx-auto">
              Follow artists from any Artist page or like/download tracks to build your personal Artists hub automatically.
            </p>
          </div>
        ) : (
          <>
            {followed.length > 0 && (
              <section className="space-y-3">
                <h2 className="text-xs font-bold uppercase tracking-[0.18em] text-[#A6B0CF]">
                  Followed Artists ({followed.length})
                </h2>
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3.5">
                  {followed.map(renderArtistCard)}
                </div>
              </section>
            )}

            {inLibrary.length > 0 && (
              <section className="space-y-3">
                <h2 className="text-xs font-bold uppercase tracking-[0.18em] text-[#A6B0CF]">
                  In Your Library ({inLibrary.length})
                </h2>
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3.5">
                  {inLibrary.map(renderArtistCard)}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
});
