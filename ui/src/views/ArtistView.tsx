import { memo, useEffect, useState, useMemo, useRef, useCallback } from 'react';
import {
  Play,
  Shuffle,
  Disc3,
  Heart,
  Download,
  CheckCircle2,
  Loader2,
  Search,
  Sparkles,
  UserRound,
  UserPlus,
  UserCheck,
  Pin,
  PinOff,
  Radio,
  UsersRound,
  ChevronDown,
  ChevronUp,
  X,
} from 'lucide-react';
import { TrackImage } from '../components/TrackImage';
import { ArtistLinks } from '../components/ArtistLinks';
import { TrackList } from '../components/TrackList';
import {
  TrackContextMenu,
  isTrackDownloaded,
  useTrackActions,
  useTrackContextMenu,
} from '../components/TrackContextMenu';
import { bridgeApi } from '../api/client';
import { useTwoFingerClose } from '../utils/gestures';
import type {
  ArtistProfile,
  ArtistRelease,
  DownloadProgress,
  Track,
} from '../api/types';

export interface ArtistNavTarget {
  name: string;
  channelId?: string | null;
}

export interface AlbumNavTarget {
  browseId?: string | null;
  title?: string | null;
  artist?: string | null;
  videoId?: string | null;
}

interface ArtistViewProps {
  target: ArtistNavTarget;
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
  onAddNext: (track: Track) => void;
  onAppendQueue: (track: Track) => void;
  onOpenAddToPlaylist: (track: Track) => void;
  onOpenAlbum: (album: AlbumNavTarget) => void;
  onOpenArtist: (artist: ArtistNavTarget) => void;
  onClose?: () => void;
  onArtistFollowChanged?: () => void;
  onArtistPinChanged?: () => void;
  onArtistRadioStarted?: () => void;
}

const artistCache = new Map<string, ArtistProfile>();
const MAX_ARTIST_CACHE = 6;

function formatDuration(secs?: number): string {
  if (!secs || isNaN(secs)) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

export const ArtistView = memo(({
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
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
  onOpenAlbum,
  onOpenArtist,
  onClose,
  onArtistFollowChanged,
  onArtistPinChanged,
  onArtistRadioStarted,
}: ArtistViewProps) => {
  const cacheKey = `${(target.channelId || '').trim()}::${target.name.trim().toLowerCase()}`;
  const containerRef = useRef<HTMLDivElement>(null);
  const [profile, setProfile] = useState<ArtistProfile | null>(
    () => artistCache.get(cacheKey) || null
  );
  const [localTracks, setLocalTracks] = useState<Track[]>([]);
  const [isFollowed, setIsFollowed] = useState<boolean>(false);
  const [isPinned, setIsPinned] = useState<boolean>(false);
  const [startingRadio, setStartingRadio] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(!artistCache.has(cacheKey));
  const [error, setError] = useState<string | null>(null);
  const [showAllPopular, setShowAllPopular] = useState<boolean>(false);
  const [expandBio, setExpandBio] = useState<boolean>(false);
  const [catalogFilter, setCatalogFilter] = useState<string>('');
  const [catalogOfflineOnly, setCatalogOfflineOnly] = useState<boolean>(false);
  const [releaseFilter, setReleaseFilter] = useState<'all' | 'albums' | 'singles'>('all');
  const [activeSection, setActiveSection] = useState<'overview' | 'catalog'>('overview');
  const reqSeqRef = useRef<number>(0);
  const { menuState, menuRef, openMenuAt, closeMenu } = useTrackContextMenu();
  const trackActions = useTrackActions();

  const syncedFollowed = trackActions.isArtistFollowed
    ? trackActions.isArtistFollowed(
        profile?.name || target.name,
        profile?.channel_id || target.channelId
      )
    : null;
  const effectiveFollowed = syncedFollowed ?? isFollowed;

  const handleRowContextMenu = useCallback(
    (e: React.MouseEvent, track: Track) => {
      e.preventDefault();
      e.stopPropagation();
      openMenuAt(
        track,
        e.clientX,
        e.clientY,
        isTrackDownloaded(track, downloadedIds, downloads)
      );
    },
    [openMenuAt, downloadedIds, downloads]
  );

  useEffect(() => {
    setCatalogFilter('');
    setCatalogOfflineOnly(false);
    setReleaseFilter('all');
    setShowAllPopular(false);
    setExpandBio(false);
    setActiveSection('overview');
    setError(null);

    const mySeq = ++reqSeqRef.current;

    // Fast < 5ms SQLite local artist tracks
    bridgeApi
      .getLocalArtistTracks({ name: target.name, channel_id: target.channelId })
      .then((rows) => {
        if (mySeq === reqSeqRef.current) setLocalTracks(rows);
      })
      .catch(() => {
        if (mySeq === reqSeqRef.current) setLocalTracks([]);
      });

    const cached = artistCache.get(cacheKey);
    if (cached) {
      setProfile(cached);
      setIsFollowed(Boolean(cached.is_followed));
      setIsPinned(Boolean(cached.is_pinned));
      setLoading(false);
    } else {
      setProfile(null);
      setLoading(true);
    }

    bridgeApi
      .getArtistProfile(target.name, target.channelId || undefined)
      .then((data) => {
        if (mySeq !== reqSeqRef.current) return;
        const hasContent = Boolean(
          data.all_tracks?.length ||
            data.popular_tracks?.length ||
            data.albums?.length ||
            data.singles?.length ||
            data.description ||
            data.thumbnail_url
        );
        if (hasContent) {
          if (artistCache.size >= MAX_ARTIST_CACHE && !artistCache.has(cacheKey)) {
            const oldest = artistCache.keys().next().value;
            if (oldest) artistCache.delete(oldest);
          }
          artistCache.set(cacheKey, data);
        }
        setProfile(data);
        setIsFollowed(Boolean(data.is_followed));
        setIsPinned(Boolean(data.is_pinned));
      })
      .catch((err: any) => {
        if (mySeq !== reqSeqRef.current) return;
        if (!cached) {
          setError(err?.message || 'Unable to load artist catalog');
        }
      })
      .finally(() => {
        if (mySeq === reqSeqRef.current) setLoading(false);
      });
  }, [cacheKey, target.name, target.channelId]);

  const popularTracks = profile?.popular_tracks || [];
  const allTracks = profile?.all_tracks || [];
  const relatedArtists = profile?.related_artists || [];

  const releases: ArtistRelease[] = useMemo(() => {
    const albs = (profile?.albums || []).map((a) => ({ ...a, type: a.type || 'Album' }));
    const sngs = (profile?.singles || []).map((s) => ({ ...s, type: s.type || 'Single' }));
    const combined =
      releaseFilter === 'albums'
        ? albs
        : releaseFilter === 'singles'
        ? sngs
        : [...albs, ...sngs];
    return [...combined].sort((a, b) => {
      const ya = parseInt(String(a.year || '0').slice(0, 4), 10) || 0;
      const yb = parseInt(String(b.year || '0').slice(0, 4), 10) || 0;
      return yb - ya;
    });
  }, [profile?.albums, profile?.singles, releaseFilter]);

  const filteredCatalog = useMemo(() => {
    let base = allTracks;
    if (catalogOfflineOnly) {
      const seen = new Set<string>();
      base = [...allTracks, ...localTracks].filter((t) => {
        if (!t.video_id || seen.has(t.video_id)) return false;
        seen.add(t.video_id);
        return isTrackDownloaded(t, downloadedIds, downloads);
      });
    } else if (base.length === 0 && localTracks.length > 0) {
      base = localTracks;
    }
    const q = catalogFilter.trim().toLowerCase();
    if (!q) return base;
    return base.filter(
      (t) =>
        t.title.toLowerCase().includes(q) ||
        (t.album || '').toLowerCase().includes(q) ||
        t.artist.toLowerCase().includes(q)
    );
  }, [allTracks, localTracks, catalogFilter, catalogOfflineOnly, downloadedIds, downloads]);

  const handleToggleFollow = async () => {
    const artistName = profile?.name || target.name;
    const channelId = profile?.channel_id || target.channelId;
    if (trackActions.onToggleFollowArtist) {
      const next = await trackActions.onToggleFollowArtist({
        name: artistName,
        channelId,
        thumbnailUrl: profile?.thumbnail_url,
      });
      setIsFollowed(next);
      if (profile) {
        const updated = { ...profile, is_followed: next };
        artistCache.set(cacheKey, updated);
        setProfile(updated);
      }
      onArtistFollowChanged?.();
      return;
    }

    const next = !effectiveFollowed;
    setIsFollowed(next);
    try {
      if (next) {
        await bridgeApi.followArtist({
          channel_id: channelId,
          name: artistName,
          thumbnail_url: profile?.thumbnail_url,
        });
      } else {
        await bridgeApi.unfollowArtist(channelId || artistName);
      }
      if (profile) {
        const updated = { ...profile, is_followed: next };
        artistCache.set(cacheKey, updated);
        setProfile(updated);
      }
      onArtistFollowChanged?.();
    } catch {
      setIsFollowed(!next);
    }
  };

  const handleTogglePinArtist = async () => {
    const artistName = profile?.name || target.name;
    const channelId = profile?.channel_id || target.channelId;
    const next = !isPinned;
    setIsPinned(next);
    try {
      if (next) {
        await bridgeApi.pinArtist({
          channel_id: channelId,
          name: artistName,
          thumbnail_url: profile?.thumbnail_url,
        });
      } else {
        await bridgeApi.unpinArtist(channelId || artistName);
      }
      if (profile) {
        const updated = { ...profile, is_pinned: next };
        artistCache.set(cacheKey, updated);
        setProfile(updated);
      }
      onArtistPinChanged?.();
    } catch {
      setIsPinned(!next);
    }
  };

  const handleStartArtistRadio = async () => {
    if (startingRadio) return;
    setStartingRadio(true);
    try {
      const result = await bridgeApi.startArtistRadio({
        channel_id: profile?.channel_id || target.channelId,
        name: profile?.name || target.name,
      });
      if (result.status === 'ok') onArtistRadioStarted?.();
    } finally {
      setStartingRadio(false);
    }
  };

  const handlePlayReleaseQuick = async (e: React.MouseEvent, rel: ArtistRelease) => {
    e.stopPropagation();
    try {
      const album = await bridgeApi.getAlbumDetails({
        browseId: rel.browse_id,
        title: rel.title,
        artist: rel.artist || profile?.name || target.name,
      });
      if (album.tracks?.length) {
        onPlayAll(album.tracks, false);
        return;
      }
    } catch {
      // Fallback to opening album view
    }
    onOpenAlbum({
      browseId: rel.browse_id,
      title: rel.title,
      artist: rel.artist || profile?.name || target.name,
    });
  };

  const visiblePopular = showAllPopular ? popularTracks.slice(0, 10) : popularTracks.slice(0, 5);

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
      className="flex-1 min-w-0 flex flex-col h-full overflow-hidden select-none ios-liquid-panel rounded-[26px] my-1"
    >
      {/* Hero Banner */}
      <div className="flex-none p-5 sm:p-6 border-b border-white/[0.08] bg-gradient-to-br from-[rgba(var(--dynamic-accent-rgb),0.12)] via-white/[0.02] to-transparent space-y-4">
        <div className="flex items-center gap-4 sm:gap-5 min-w-0">
          <div
            className="w-20 h-20 sm:w-24 sm:h-24 aspect-square shrink-0 rounded-full bg-[#06080F] border border-white/20 flex items-center justify-center shadow-xl overflow-hidden"
            style={{ boxShadow: '0 10px 30px rgba(0,0,0,0.65), 0 0 24px rgba(var(--dynamic-accent-rgb),0.18)' }}
          >
            {profile?.thumbnail_url ? (
              <TrackImage
                src={profile.thumbnail_url}
                size="md"
                iconSize={32}
                className="w-full h-full object-cover aspect-square"
                eager
              />
            ) : (
              <UserRound size={34} className="text-[#8E99BA]" />
            )}
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <Sparkles size={11} style={{ color: 'var(--dynamic-accent)' }} className="flex-none" />
              <span
                className="text-[10px] uppercase tracking-[0.22em] font-bold"
                style={{ color: 'var(--dynamic-accent)' }}
              >
                Artist Library
              </span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold text-[#F4F6FF] tracking-tight truncate mt-0.5 leading-tight">
              {profile?.name || target.name}
            </h1>
            <div className="flex items-center gap-x-3 gap-y-1 flex-wrap text-xs text-[#9AA5C4] mt-1.5">
              {profile?.subscribers && (
                <span className="whitespace-nowrap">{profile.subscribers} subscribers</span>
              )}
              {profile?.views && (
                <span className="whitespace-nowrap">• {profile.views}</span>
              )}
              {!loading && allTracks.length > 0 && (
                <span className="whitespace-nowrap">• {allTracks.length} songs</span>
              )}
              {!loading && releases.length > 0 && (
                <span className="whitespace-nowrap">• {releases.length} releases</span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {allTracks.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => onPlayAll(popularTracks.length > 0 ? popularTracks : allTracks, false)}
                className="px-4 py-2 bg-[#F4F6FF] text-[#020308] font-semibold text-xs rounded-full flex items-center gap-2 hover:scale-[1.02] active:scale-95 transition-transform shadow-md"
              >
                <Play size={14} className="fill-current" />
                Play Popular
              </button>
              <button
                type="button"
                onClick={() => onPlayAll(allTracks, true)}
                className="px-3.5 py-2 ios-liquid-pill text-[#F4F6FF] font-medium text-xs rounded-full flex items-center gap-1.5"
              >
                <Shuffle size={14} />
                Shuffle
              </button>
            </>
          )}
          <button
            type="button"
            disabled={startingRadio}
            onClick={() => void handleStartArtistRadio()}
            className="px-3.5 py-2 ios-liquid-pill text-[#F4F6FF] font-medium text-xs rounded-full flex items-center gap-1.5 disabled:opacity-50"
          >
            <Radio size={14} style={{ color: 'var(--dynamic-accent)' }} />
            {startingRadio ? 'Starting...' : 'Radio'}
          </button>
          <button
            type="button"
            onClick={() => void handleToggleFollow()}
            className={`px-3.5 py-2 text-xs font-medium rounded-full flex items-center gap-1.5 border transition-colors ${
              effectiveFollowed
                ? 'bg-white/[0.16] border-white/25 text-white'
                : 'ios-liquid-pill text-[#D4DCFA] border-white/10'
            }`}
          >
            {effectiveFollowed ? <UserCheck size={14} className="text-[#34D399]" /> : <UserPlus size={14} />}
            {effectiveFollowed ? 'Following' : 'Follow'}
          </button>
          <button
            type="button"
            onClick={() => void handleTogglePinArtist()}
            title={isPinned ? 'Unpin Artist from Speed Dial' : 'Pin Artist to Speed Dial'}
            className={`p-2 rounded-full border transition-colors ${
              isPinned
                ? 'bg-white/[0.16] border-white/25 text-[#F59E0B]'
                : 'ios-liquid-pill text-[#A6B0CF] hover:text-white border-white/10'
            }`}
          >
            {isPinned ? <PinOff size={14} /> : <Pin size={14} />}
          </button>
        </div>

        {/* Section Tabs */}
        <div className="mt-4 flex items-center justify-between gap-3 flex-wrap">
          <div className="inline-flex items-center gap-1 p-1 rounded-full bg-white/[0.05] border border-white/[0.09]">
            <button
              type="button"
              onClick={() => setActiveSection('overview')}
              className={`px-3.5 py-1 rounded-full text-xs font-medium transition-colors ${
                activeSection === 'overview'
                  ? 'bg-[#F4F6FF] text-[#020308] font-semibold'
                  : 'text-[#A6B0CF] hover:text-white'
              }`}
            >
              Overview & Popular
            </button>
            <button
              type="button"
              onClick={() => setActiveSection('catalog')}
              className={`px-3.5 py-1 rounded-full text-xs font-medium transition-colors ${
                activeSection === 'catalog'
                  ? 'bg-[#F4F6FF] text-[#020308] font-semibold'
                  : 'text-[#A6B0CF] hover:text-white'
              }`}
            >
              Full Catalog ({allTracks.length})
            </button>
          </div>

          {activeSection === 'catalog' && allTracks.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap w-full sm:w-auto">
              <div className="inline-flex items-center gap-0.5 p-0.5 rounded-full bg-white/[0.05] border border-white/[0.09]">
                <button
                  type="button"
                  onClick={() => setCatalogOfflineOnly(false)}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors ${
                    !catalogOfflineOnly ? 'bg-[#F4F6FF] text-[#020308] font-semibold' : 'text-[#A6B0CF]'
                  }`}
                >
                  All
                </button>
                <button
                  type="button"
                  onClick={() => setCatalogOfflineOnly(true)}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors ${
                    catalogOfflineOnly ? 'bg-[#34D399] text-[#020308] font-semibold' : 'text-[#A6B0CF]'
                  }`}
                >
                  Offline Only
                </button>
              </div>
              <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-black/40 border border-white/12 flex-1 sm:w-56">
              <Search size={12} className="text-[#8E99BA] flex-none" />
              <input
                type="text"
                value={catalogFilter}
                onChange={(e) => setCatalogFilter(e.target.value)}
                placeholder={`Filter ${profile?.name || target.name} songs...`}
                className="bg-transparent text-xs text-[#F4F6FF] placeholder-[#6D7796] focus:outline-none flex-1 min-w-0"
              />
                {catalogFilter && (
                <button
                  type="button"
                  onClick={() => setCatalogFilter('')}
                  className="text-[#8E99BA] hover:text-white"
                >
                  <X size={12} />
                </button>
              )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Body */}
      {loading && !profile ? (
        <div className="flex-1 overflow-hidden p-5 space-y-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <div
              key={i}
              className="h-14 rounded-xl bg-white/[0.04] border border-white/[0.06] px-4 flex items-center gap-3 eh-skeleton"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <div className="w-10 h-10 rounded-xl bg-white/[0.08]" />
              <div className="flex-1 space-y-1.5">
                <div className="h-3 w-48 rounded-full bg-white/[0.09]" />
                <div className="h-2.5 w-28 rounded-full bg-white/[0.06]" />
              </div>
            </div>
          ))}
        </div>
      ) : error && !profile && localTracks.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-[#A6B0CF] gap-2">
          <UserRound size={32} className="opacity-35" />
          <p className="text-sm font-semibold text-[#F4F6FF]">Could not load artist library</p>
          <p className="text-xs text-[#7A84A6]">{error}</p>
        </div>
      ) : activeSection === 'catalog' ? (
        <TrackList
          tracks={filteredCatalog}
          scrollKey={`${cacheKey}:${catalogFilter}`}
          currentTrackId={currentTrackId}
          isPlaying={isPlaying}
          downloads={downloads}
          likedIds={likedIds}
          downloadedIds={downloadedIds}
          onPlay={(track, idx) => onPlay(track, idx, filteredCatalog)}
          onToggleLike={onToggleLike}
          onDownload={onDownload}
          onDeleteDownload={onDeleteDownload}
          onAddNext={onAddNext}
          onAppendQueue={onAppendQueue}
          onOpenAddToPlaylist={onOpenAddToPlaylist}
          onOpenArtist={(artistName, artistId) => onOpenArtist({ name: artistName, channelId: artistId })}
          onOpenAlbum={(albumName, albumId, track) =>
            onOpenAlbum({
              browseId: albumId,
              title: albumName,
              artist: track.artist,
              videoId: track.video_id,
            })
          }
          emptyMessage="No matching tracks in this artist's catalog."
        />
      ) : (
        <div
          style={{ contain: 'strict', willChange: 'scroll-position' }}
          className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden px-5 sm:px-6 py-5 space-y-7"
        >
          {/* Local-first Your Songs Shelf */}
          {localTracks.length > 0 && (
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold uppercase tracking-[0.16em] text-[#E6ECF8]">
                  Your Songs ({localTracks.length})
                </h2>
                <button
                  type="button"
                  onClick={() => onPlayAll(localTracks, false)}
                  className="text-[11px] font-medium px-3 py-1 rounded-full ios-liquid-pill text-[#F4F6FF]"
                >
                  Play Local Mix
                </button>
              </div>
              <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] overflow-hidden divide-y divide-white/[0.05]">
                {localTracks.slice(0, 6).map((track, idx) => {
                  const isCurrent = currentTrackId === track.video_id;
                  const isOff = isTrackDownloaded(track, downloadedIds, downloads);
                  const isLiked = likedIds ? likedIds.has(track.video_id) : !!track.is_liked;
                  return (
                    <div
                      key={`local-${track.video_id}-${idx}`}
                      onClick={() => onPlay(track, idx, localTracks)}
                      onContextMenu={(e) => handleRowContextMenu(e, track)}
                      className={`flex items-center px-3.5 py-2 gap-3 hover:bg-white/[0.06] group cursor-pointer transition-colors ${
                        isCurrent ? 'bg-white/[0.08]' : ''
                      }`}
                    >
                      <div className="w-9 h-9 rounded-lg bg-black/70 border border-white/15 overflow-hidden flex-none">
                        <TrackImage
                          src={track.thumbnail_url}
                          videoId={track.video_id}
                          size="sm"
                          iconSize={15}
                        />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div
                          className="text-xs font-semibold truncate"
                          style={isCurrent ? { color: 'var(--dynamic-accent)' } : { color: '#F4F6FF' }}
                        >
                          {track.title}
                        </div>
                        <div className="text-[11px] text-[#9AA5C4] truncate">
                          {track.album || track.artist}
                        </div>
                      </div>
                      {isOff && (
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[#34D399]/15 text-[#34D399]">
                          Offline
                        </span>
                      )}
                      {isLiked && (
                        <Heart size={13} className="fill-[#FF4D8D] text-[#FF4D8D] flex-none" />
                      )}
                      <span className="text-[11px] font-mono tabular-nums text-[#8E99BA] flex-none">
                        {track.duration_s > 0 ? formatDuration(track.duration_s) : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {/* Popular Tracks */}
          {popularTracks.length > 0 && (
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold uppercase tracking-[0.16em] text-[#E6ECF8]">
                  Popular Tracks
                </h2>
                {popularTracks.length > 5 && (
                  <button
                    type="button"
                    onClick={() => setShowAllPopular((s) => !s)}
                    className="text-[11px] text-[#9AA5C4] hover:text-white inline-flex items-center gap-1"
                  >
                    <span>{showAllPopular ? 'Show less' : `See top ${Math.min(10, popularTracks.length)}`}</span>
                    {showAllPopular ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  </button>
                )}
              </div>

              <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] overflow-hidden divide-y divide-white/[0.05]">
                {visiblePopular.map((track, idx) => {
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
                      key={`${track.video_id}-${idx}`}
                      onClick={() => onPlay(track, idx, popularTracks)}
                      onContextMenu={(e) => handleRowContextMenu(e, track)}
                      className={`flex items-center px-3.5 py-2.5 gap-3 hover:bg-white/[0.06] group cursor-pointer transition-colors ${
                        isCurrent ? 'bg-white/[0.08]' : ''
                      }`}
                    >
                      <span
                        className="w-5 text-center text-xs font-mono tabular-nums flex-none"
                        style={isCurrent ? { color: 'var(--dynamic-accent)' } : { color: '#7A84A6' }}
                      >
                        {idx + 1}
                      </span>

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
                          style={isCurrent ? { color: 'var(--dynamic-accent)' } : { color: '#F4F6FF' }}
                        >
                          {track.title}
                        </div>
                        <div className="text-[11px] text-[#9AA5C4] truncate">
                          {track.album ? (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onOpenAlbum({
                                  browseId: track.album_id,
                                  title: track.album,
                                  artist: track.artist,
                                  videoId: track.video_id,
                                });
                              }}
                              className="hover:text-white hover:underline truncate max-w-full"
                            >
                              {track.album}
                            </button>
                          ) : (
                            <ArtistLinks
                              artist={track.artist}
                              artistId={track.artist_id}
                              artists={track.artists}
                              onOpenArtist={(artistName, artistId) =>
                                onOpenArtist({ name: artistName, channelId: artistId })
                              }
                              className="text-[11px] text-[#9AA5C4]"
                            />
                          )}
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleLike(track);
                        }}
                        title={isLiked ? 'Unlike' : 'Like'}
                        className="p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] transition-colors flex-none"
                      >
                        <Heart
                          size={14}
                          className={isLiked ? 'fill-[#FF4D8D] text-[#FF4D8D]' : 'opacity-60 group-hover:opacity-100'}
                        />
                      </button>

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (!isDownloaded && !isDownloading) onDownload(track);
                        }}
                        title={isDownloaded ? 'Downloaded offline' : 'Download track'}
                        className="hidden sm:inline-flex p-1.5 text-[#A6B0CF] hover:text-[#34D399] transition-colors flex-none"
                      >
                        {isDownloaded ? (
                          <CheckCircle2 size={14} className="text-[#34D399]" />
                        ) : isDownloading ? (
                          <Loader2 size={14} className="animate-spin" style={{ color: 'var(--dynamic-accent)' }} />
                        ) : (
                          <Download size={14} className="opacity-0 group-hover:opacity-80" />
                        )}
                      </button>

                      <span className="hidden sm:block w-11 text-right text-[11px] font-mono tabular-nums text-[#8E99BA] flex-none">
                        {track.duration_s > 0 ? formatDuration(track.duration_s) : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {/* Albums & Singles Discography */}
          {((profile?.albums?.length || 0) > 0 || (profile?.singles?.length || 0) > 0) && (
            <section className="space-y-3">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2">
                  <Disc3 size={15} style={{ color: 'var(--dynamic-accent)' }} />
                  <h2 className="text-sm font-bold uppercase tracking-[0.16em] text-[#E6ECF8]">
                    Albums & Singles
                  </h2>
                </div>
                <div className="flex items-center gap-2">
                  <div className="inline-flex items-center gap-0.5 p-0.5 rounded-full bg-white/[0.05] border border-white/[0.09]">
                    {(['all', 'albums', 'singles'] as const).map((rf) => (
                      <button
                        key={rf}
                        type="button"
                        onClick={() => setReleaseFilter(rf)}
                        className={`px-2.5 py-0.5 rounded-full text-[11px] font-medium transition-colors ${
                          releaseFilter === rf
                            ? 'bg-[#F4F6FF] text-[#020308] font-semibold'
                            : 'text-[#A6B0CF] hover:text-white'
                        }`}
                      >
                        {rf === 'all' ? 'All' : rf === 'albums' ? 'Albums' : 'Singles & EPs'}
                      </button>
                    ))}
                  </div>
                  <span className="text-[11px] text-[#7A84A6] font-mono tabular-nums">
                    {releases.length}
                  </span>
                </div>
              </div>

              <div className="flex gap-3.5 overflow-x-auto no-scrollbar pb-2">
                {releases.slice(0, 20).map((rel) => (
                  <div
                    key={rel.browse_id}
                    onClick={() =>
                      onOpenAlbum({
                        browseId: rel.browse_id,
                        title: rel.title,
                        artist: rel.artist || profile?.name || target.name,
                      })
                    }
                    className="group ios-liquid-card w-[158px] rounded-2xl p-2.5 flex-none text-left flex flex-col cursor-pointer"
                  >
                    <div className="relative aspect-square w-full rounded-xl overflow-hidden bg-[#06080F] border border-white/[0.08]">
                      <TrackImage
                        src={rel.thumbnail_url}
                        size="md"
                        iconSize={26}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                      />
                      <button
                        type="button"
                        title={`Play ${rel.title}`}
                        onClick={(e) => void handlePlayReleaseQuick(e, rel)}
                        className="absolute bottom-2 right-2 w-8 h-8 rounded-full bg-white text-black flex items-center justify-center opacity-0 group-hover:opacity-100 translate-y-1 group-hover:translate-y-0 transition-all shadow-lg"
                      >
                        <Play size={13} className="fill-current ml-0.5" />
                      </button>
                    </div>
                    <div className="mt-2.5 px-0.5">
                      <div className="text-xs font-semibold text-[#F4F6FF] truncate">
                        {rel.title}
                      </div>
                      <div className="text-[11px] text-[#9AA5C4] truncate mt-0.5">
                        {[rel.year, rel.type].filter(Boolean).join(' • ') || 'Release'}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Fans Also Like Carousel */}
          {relatedArtists.length > 0 && (
            <section className="space-y-3">
              <div className="flex items-center gap-2">
                <UsersRound size={15} style={{ color: 'var(--dynamic-accent)' }} />
                <h2 className="text-sm font-bold uppercase tracking-[0.16em] text-[#E6ECF8]">
                  Fans Also Like
                </h2>
              </div>
              <div className="flex gap-3.5 overflow-x-auto no-scrollbar pb-2">
                {relatedArtists.map((rel, rIdx) => (
                  <button
                    key={`${rel.browse_id || rel.name}-${rIdx}`}
                    type="button"
                    onClick={() =>
                      onOpenArtist({
                        name: rel.name,
                        channelId: rel.channel_id || rel.browse_id,
                      })
                    }
                    className="group ios-liquid-card w-[136px] rounded-2xl p-3 flex-none flex flex-col items-center text-center cursor-pointer"
                  >
                    <div className="w-20 h-20 aspect-square shrink-0 rounded-full overflow-hidden bg-[#06080F] border border-white/15 flex items-center justify-center">
                      {rel.thumbnail_url ? (
                        <TrackImage
                          src={rel.thumbnail_url}
                          size="md"
                          iconSize={24}
                          className="w-full h-full object-cover aspect-square group-hover:scale-105 transition-transform duration-300"
                        />
                      ) : (
                        <UserRound size={24} className="text-[#8E99BA]" />
                      )}
                    </div>
                    <div className="mt-2.5 text-xs font-semibold text-[#F4F6FF] truncate w-full">
                      {rel.name}
                    </div>
                    {rel.subscribers && (
                      <div className="text-[10.5px] text-[#8E99BA] truncate w-full mt-0.5">
                        {rel.subscribers}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* Call-to-action to Full Catalog */}
          {allTracks.length > visiblePopular.length && (
            <div className="flex items-center justify-between p-4 rounded-2xl border border-white/[0.08] bg-white/[0.025]">
              <div>
                <div className="text-xs font-semibold text-[#F4F6FF]">
                  Complete Discography ({allTracks.length} songs)
                </div>
                <div className="text-[11px] text-[#9AA5C4] mt-0.5">
                  Browse, filter, and play every released song by {profile?.name || target.name}.
                </div>
              </div>
              <button
                type="button"
                onClick={() => setActiveSection('catalog')}
                className="px-4 py-1.5 rounded-full text-xs font-semibold ios-liquid-pill text-[#F4F6FF] hover:text-white flex-none"
              >
                Open Full Catalog
              </button>
            </div>
          )}

          {/* About Artist */}
          {profile?.description && (
            <section className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4 space-y-2">
              <h3 className="text-xs font-bold uppercase tracking-[0.16em] text-[#9AA5C4]">
                About
              </h3>
              <p
                className={`text-xs leading-relaxed text-[#C8D1EE] whitespace-pre-line ${
                  expandBio ? '' : 'line-clamp-3'
                }`}
              >
                {profile.description}
              </p>
              {profile.description.length > 220 && (
                <button
                  type="button"
                  onClick={() => setExpandBio((b) => !b)}
                  className="text-[11px] font-semibold hover:underline"
                  style={{ color: 'var(--dynamic-accent)' }}
                >
                  {expandBio ? 'Show less' : 'Read more'}
                </button>
              )}
            </section>
          )}
        </div>
      )}

      <TrackContextMenu
        menuState={menuState}
        menuRef={menuRef}
        onClose={closeMenu}
        onAddNext={onAddNext}
        onAppendQueue={onAppendQueue}
        onOpenArtist={(artistName, artistId) => onOpenArtist({ name: artistName, channelId: artistId })}
        onOpenAlbum={(albumName, albumId, track) =>
          onOpenAlbum({
            browseId: albumId,
            title: albumName,
            artist: track.artist,
            videoId: track.video_id,
          })
        }
        onOpenAddToPlaylist={onOpenAddToPlaylist}
        onDownload={onDownload}
        onDeleteDownload={onDeleteDownload}
      />
    </div>
  );
});
