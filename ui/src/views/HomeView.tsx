import { memo, useEffect, useState, useCallback, useRef } from 'react';
import {
  Play,
  Sparkles,
  Flame,
  RefreshCw,
  Headphones,
  Coffee,
  Zap,
  Moon,
  Compass,
  Heart,
  TrendingUp,
  ChevronLeft,
  ChevronRight,
  Radio,
} from 'lucide-react';
import { TrackImage } from '../components/TrackImage';
import { ArtistLinks } from '../components/ArtistLinks';
import {
  TrackContextMenu,
  isTrackDownloaded,
  useTrackContextMenu,
} from '../components/TrackContextMenu';
import type {
  Track,
  HomeSection,
  PersonalizedHomeResponse,
  DownloadProgress,
} from '../api/types';
import { bridgeApi, cancelTrackPreload, scheduleTrackPreload } from '../api/client';

interface HomeViewProps {
  currentTrackId?: string | null;
  isConnected?: boolean;
  likedIds?: Set<string>;
  downloads?: Record<string, DownloadProgress>;
  downloadedIds?: Set<string>;
  onPlayTrack: (track: Track) => void;
  onPlayAll: (tracks: Track[]) => void;
  onToggleLike: (track: Track) => void;
  onAddNext: (track: Track) => void;
  onAppendQueue?: (track: Track) => void;
  onOpenAddToPlaylist?: (track: Track) => void;
  onDownload?: (track: Track) => void;
  onDeleteDownload?: (track: Track) => void;
  onOpenArtist?: (artistName: string, artistId?: string | null) => void;
  onOpenAlbum?: (albumName: string, albumId: string | null | undefined, track: Track) => void;
}

function hasPlayableContent(data: PersonalizedHomeResponse | null | undefined): boolean {
  if (!data) return false;
  if (Array.isArray(data.speed_dial) && data.speed_dial.length > 0) return true;
  return (data.sections || []).some(
    (sec) =>
      Array.isArray(sec.contents) &&
      sec.contents.some((c) => Boolean(c && typeof c === 'object' && (c as Track).video_id))
  );
}

const MOOD_PRESETS: { id: string; label: string }[] = [
  { id: 'for_you', label: 'For You' },
  { id: 'trending', label: 'Trending' },
  { id: 'bollywood', label: 'Bollywood' },
  { id: 'chill', label: 'Chill & Lo-Fi' },
  { id: 'focus', label: 'Focus' },
  { id: 'workout', label: 'Workout' },
  { id: 'latenight', label: 'Late Night' },
];

const cachedByMood = new Map<string, PersonalizedHomeResponse>();

function getStableGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

const HorizontalShelf = ({
  title,
  subtitle,
  icon,
  tracks,
  currentTrackId,
  likedIds,
  onPlayTrack,
  onPlayAll,
  onToggleLike,
  onAddNext,
  onOpenArtist,
  onContextMenuTrack,
}: {
  title: string;
  subtitle?: string;
  icon: React.ReactNode;
  tracks: Track[];
  currentTrackId?: string | null;
  likedIds?: Set<string>;
  onPlayTrack: (t: Track) => void;
  onPlayAll: (ts: Track[]) => void;
  onToggleLike: (t: Track) => void;
  onAddNext: (t: Track) => void;
  onOpenArtist?: (artistName: string, artistId?: string | null) => void;
  onContextMenuTrack?: (e: React.MouseEvent, t: Track) => void;
}) => {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [fadeClass, setFadeClass] = useState<string>('shelf-fade-right');

  const updateEdgeMask = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const maxScroll = el.scrollWidth - el.clientWidth;
    if (maxScroll <= 8) {
      setFadeClass('');
      return;
    }
    const atLeft = el.scrollLeft <= 6;
    const atRight = el.scrollLeft >= maxScroll - 6;
    if (atLeft && !atRight) setFadeClass('shelf-fade-right');
    else if (!atLeft && !atRight) setFadeClass('shelf-fade-both');
    else if (!atLeft && atRight) setFadeClass('shelf-fade-left');
    else setFadeClass('');
  }, []);

  useEffect(() => {
    updateEdgeMask();
    window.addEventListener('resize', updateEdgeMask);
    return () => window.removeEventListener('resize', updateEdgeMask);
  }, [tracks.length, updateEdgeMask]);

  const scrollBy = (delta: number) => {
    scrollerRef.current?.scrollBy({ left: delta, behavior: 'smooth' });
  };

  return (
    <section className="space-y-3.5 eh-shelf-section">
      <div className="flex items-end justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            {icon}
            <h2 className="text-lg font-bold tracking-tight text-[#F4F6FF]">{title}</h2>
          </div>
          {subtitle && <p className="text-[11px] text-[#A6B0CF] mt-0.5">{subtitle}</p>}
        </div>

        <div className="flex items-center gap-1.5 flex-none">
          <button
            onClick={() => onPlayAll(tracks)}
            className="text-[11px] text-[#F4F6FF] font-medium px-3 py-1 rounded-full ios-liquid-pill mr-1"
          >
            Play All
          </button>
          <button
            onClick={() => scrollBy(-460)}
            title="Scroll Left"
            className="w-7 h-7 rounded-full ios-liquid-pill flex items-center justify-center text-[#A6B0CF] hover:text-white"
          >
            <ChevronLeft size={14} />
          </button>
          <button
            onClick={() => scrollBy(460)}
            title="Scroll Right"
            className="w-7 h-7 rounded-full ios-liquid-pill flex items-center justify-center text-[#A6B0CF] hover:text-white"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      <div
        ref={scrollerRef}
        onScroll={updateEdgeMask}
        className={`flex gap-3.5 overflow-x-auto no-scrollbar pt-1.5 pb-3 pr-8 -mt-1.5 ${fadeClass}`}
      >
        {tracks.slice(0, 10).map((item, idx) => {
          const isCurrent = currentTrackId === item.video_id;
          const isLiked = likedIds ? likedIds.has(item.video_id) : !!item.is_liked;

          return (
            <div
              key={`${item.video_id}-${idx}`}
              onClick={() => onPlayTrack(item)}
              onMouseEnter={() => scheduleTrackPreload(item)}
              onMouseLeave={() => cancelTrackPreload(item.video_id)}
              onContextMenu={(e) => onContextMenuTrack?.(e, item)}
              className="group ios-liquid-card w-[168px] rounded-2xl p-2.5 flex-none cursor-pointer flex flex-col"
              style={
                isCurrent
                  ? { borderColor: 'rgba(var(--dynamic-accent-rgb), 0.65)' }
                  : undefined
              }
            >
              <div className="relative aspect-square w-full rounded-xl overflow-hidden bg-[#06080F] border border-white/[0.08]">
                <TrackImage
                  src={item.thumbnail_url}
                  videoId={item.video_id}
                  size="md"
                  iconSize={26}
                  className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                />

                {/* Contrast scrim so action buttons pop cleanly over light album art */}
                <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-black/55 via-transparent to-black/65 opacity-0 group-hover:opacity-100 transition-opacity duration-200" />

                <div className="absolute top-2 right-2 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200 z-10">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onAddNext(item);
                    }}
                    title="Play Next"
                    className="w-7 h-7 rounded-full bg-black/80 hover:bg-black text-[#F4F6FF] border border-white/25 shadow-md flex items-center justify-center transition-colors"
                  >
                    <Radio size={12} style={{ color: 'var(--dynamic-accent)' }} />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleLike(item);
                    }}
                    title={isLiked ? 'Unlike' : 'Like'}
                    className="w-7 h-7 rounded-full bg-black/80 hover:bg-black text-[#F4F6FF] border border-white/25 shadow-md flex items-center justify-center transition-colors"
                  >
                    <Heart size={12} className={isLiked ? 'fill-[#FF4D8D] text-[#FF4D8D]' : ''} />
                  </button>
                </div>

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onPlayTrack(item);
                  }}
                  className="absolute bottom-2 right-2 w-9 h-9 rounded-full ios-liquid-orb text-[#020308] flex items-center justify-center opacity-0 group-hover:opacity-100 translate-y-1.5 group-hover:translate-y-0 transition-all duration-200 z-10"
                >
                  <Play size={15} className="fill-current ml-0.5" />
                </button>

                {(item.play_count ?? 0) > 1 && (
                  <span className="absolute top-2 left-2 text-[10px] font-mono tabular-nums font-semibold text-white bg-black/80 border border-white/20 px-1.5 py-0.5 rounded-full z-10">
                    {item.play_count}x
                  </span>
                )}
              </div>

              <div className="mt-2.5 px-0.5">
                <div
                  className="text-xs font-semibold truncate"
                  style={isCurrent ? { color: 'var(--dynamic-accent)' } : { color: '#F4F6FF' }}
                >
                  {item.title}
                </div>
                <ArtistLinks
                  artist={item.artist}
                  artistId={item.artist_id}
                  artists={item.artists}
                  onOpenArtist={onOpenArtist}
                  className="text-[11px] text-[#A6B0CF] mt-0.5"
                />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};

export const HomeView = memo(({
  currentTrackId,
  isConnected = true,
  likedIds,
  downloads,
  downloadedIds,
  onPlayTrack,
  onPlayAll,
  onToggleLike,
  onAddNext,
  onAppendQueue,
  onOpenAddToPlaylist,
  onDownload,
  onDeleteDownload,
  onOpenArtist,
  onOpenAlbum,
}: HomeViewProps) => {
  const [activeMood, setActiveMood] = useState<string>('for_you');
  const [homeData, setHomeData] = useState<PersonalizedHomeResponse | null>(
    cachedByMood.get('for_you') || null
  );
  const [isLoading, setIsLoading] = useState<boolean>(!cachedByMood.has('for_you'));
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [greeting] = useState<string>(() => getStableGreeting());
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const { menuState, menuRef, openMenuAt, closeMenu } = useTrackContextMenu();
  const handleTrackContextMenu = useCallback(
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
  const reqSeqRef = useRef<number>(0);
  const bhCanvasRef = useRef<HTMLElement | null>(null);
  const scrollRafRef = useRef<number>(0);
  const lastOpacityRef = useRef<number>(1);

  useEffect(() => {
    return () => {
      reqSeqRef.current += 1;
      if (scrollRafRef.current) {
        cancelAnimationFrame(scrollRafRef.current);
        scrollRafRef.current = 0;
      }
    };
  }, []);

  // Subtly dim ONLY the StarfieldBg canvas (never touch documentElement CSS vars during scroll!)
  useEffect(() => {
    const canvas = document.querySelector<HTMLElement>('[data-bh-canvas="true"]');
    bhCanvasRef.current = canvas;
    if (canvas) canvas.style.opacity = '1';
    lastOpacityRef.current = 1;
    return () => {
      if (bhCanvasRef.current) bhCanvasRef.current.style.opacity = '1';
    };
  }, []);

  const handleScroll = useCallback(() => {
    if (scrollRafRef.current) return;
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = 0;
      const el = scrollContainerRef.current;
      if (!el) return;
      const progress = Math.min(1, el.scrollTop / 240);
      const next = Math.round((1 - progress * 0.56) * 100) / 100;
      if (Math.abs(next - lastOpacityRef.current) < 0.01) return;
      lastOpacityRef.current = next;
      const canvas = bhCanvasRef.current || document.querySelector<HTMLElement>('[data-bh-canvas="true"]');
      if (canvas) {
        bhCanvasRef.current = canvas;
        canvas.style.opacity = String(next);
      }
    });
  }, []);

  const prevConnectedRef = useRef<boolean>(isConnected);

  const loadFeed = useCallback(async (mood: string, forceRefresh: boolean) => {
    const mySeq = ++reqSeqRef.current;

    if (!forceRefresh && cachedByMood.has(mood)) {
      const cached = cachedByMood.get(mood) || null;
      if (hasPlayableContent(cached)) {
        setHomeData(cached);
        setIsLoading(false);
        setIsRefreshing(false);
        return;
      }
      cachedByMood.delete(mood);
    }

    if (forceRefresh) {
      setIsRefreshing(true);
    } else {
      setHomeData(null);
      setIsLoading(true);
    }

    try {
      const data = await bridgeApi.getPersonalizedHome(mood, forceRefresh);
      if (hasPlayableContent(data)) {
        if (cachedByMood.size >= 3 && !cachedByMood.has(mood)) {
          const oldest = cachedByMood.keys().next().value;
          if (oldest) cachedByMood.delete(oldest);
        }
        cachedByMood.set(mood, data);
        if (mySeq !== reqSeqRef.current) return;
        setHomeData(data);
        return;
      }
      cachedByMood.delete(mood);
      const basic = await bridgeApi.getHome();
      const fallbackData: PersonalizedHomeResponse = {
        greeting: data.greeting || getStableGreeting(),
        speed_dial: [],
        sections: basic,
        personalized: false,
      };
      if (mySeq !== reqSeqRef.current) return;
      setHomeData(
        hasPlayableContent(fallbackData)
          ? fallbackData
          : { greeting: fallbackData.greeting, speed_dial: [], sections: [], personalized: false }
      );
    } catch {
      try {
        const basic = await bridgeApi.getHome();
        const fallbackData: PersonalizedHomeResponse = {
          greeting: getStableGreeting(),
          speed_dial: [],
          sections: basic,
          personalized: false,
        };
        if (mySeq !== reqSeqRef.current) return;
        setHomeData(
          hasPlayableContent(fallbackData)
            ? fallbackData
            : { greeting: fallbackData.greeting, speed_dial: [], sections: [], personalized: false }
        );
      } catch {
        if (mySeq !== reqSeqRef.current) return;
        if (!forceRefresh) {
          setHomeData({
            greeting: getStableGreeting(),
            speed_dial: [],
            sections: [],
            personalized: false,
          });
        }
      }
    } finally {
      if (mySeq === reqSeqRef.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadFeed(activeMood, false);
  }, [activeMood, loadFeed]);

  useEffect(() => {
    const wasConnected = prevConnectedRef.current;
    prevConnectedRef.current = isConnected;
    if (!wasConnected && isConnected && !isLoading && !hasPlayableContent(homeData)) {
      void loadFeed(activeMood, false);
    }
  }, [isConnected, isLoading, homeData, activeMood, loadFeed]);

  const getSectionIcon = (title: string) => {
    const lower = title.toLowerCase();
    if (lower.includes('quick') || lower.includes('pick') || lower.includes('because')) {
      return <Sparkles size={16} style={{ color: 'var(--dynamic-accent)' }} />;
    }
    if (lower.includes('most played') || lower.includes('replay')) {
      return <TrendingUp size={16} className="text-[#34D399]" />;
    }
    if (lower.includes('trend') || lower.includes('top') || lower.includes('bollywood')) {
      return <Flame size={16} className="text-[#FFB347]" />;
    }
    if (lower.includes('chill') || lower.includes('lofi')) {
      return <Coffee size={16} className="text-[#FFB347]" />;
    }
    if (lower.includes('focus') || lower.includes('study')) {
      return <Headphones size={16} className="text-[#7C5CFF]" />;
    }
    if (lower.includes('workout')) {
      return <Zap size={16} className="text-[#4DE1FF]" />;
    }
    if (lower.includes('night')) {
      return <Moon size={16} className="text-[#A6B0CF]" />;
    }
    return <Compass size={16} className="text-[#A6B0CF]" />;
  };

  const speedDial = homeData?.speed_dial || [];
  const sections: HomeSection[] = homeData?.sections || [];
  const hasRenderableContent = hasPlayableContent(homeData);

  return (
    <div
      ref={scrollContainerRef}
      onScroll={handleScroll}
      style={{ contain: 'strict', willChange: 'scroll-position' }}
      className="flex-1 overflow-y-auto select-none"
    >
      <div className="px-6 pt-3 pb-10 space-y-7 max-w-[1440px] mx-auto">
        {/* Clean Dark Header — Top-right stays open for the subtle black hole */}
        <div className="pt-2 pb-2 space-y-3.5 max-w-2xl">
          <div className="space-y-1">
            <div className="flex items-center gap-2.5">
              <span className="text-[10px] font-bold uppercase tracking-[0.26em] text-[#8E99BA]">
                {homeData?.personalized ? 'Personal Orbit' : 'Event Horizon'}
              </span>
              <button
                onClick={() => void loadFeed(activeMood, true)}
                disabled={isRefreshing}
                title="Refresh personalized picks & trends"
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium ios-liquid-pill text-[#E6ECF8] disabled:opacity-50"
              >
                <RefreshCw
                  size={11}
                  className={isRefreshing ? 'animate-spin' : ''}
                  style={{ color: 'var(--dynamic-accent)' }}
                />
                <span>{isRefreshing ? 'Refreshing' : 'Refresh'}</span>
              </button>
            </div>
            <h1
              className="text-3xl sm:text-4xl font-bold tracking-tight text-[#F1F4FA]"
              style={{ textShadow: '0 2px 16px rgba(6, 8, 15, 0.85)' }}
            >
              {greeting}
            </h1>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {MOOD_PRESETS.map((mood) => {
              const isSelected = activeMood === mood.id;
              return (
                <button
                  key={mood.id}
                  onClick={() => setActiveMood(mood.id)}
                  className={`px-3.5 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all ${
                    isSelected
                      ? 'bg-[#F1F4FA] text-[#06080F] font-semibold shadow-sm'
                      : 'ios-liquid-pill text-[#9AA5C4] hover:text-white'
                  }`}
                >
                  {mood.label}
                </button>
              );
            })}
          </div>
        </div>

        {isLoading && !hasRenderableContent ? (
          <div className="space-y-7 eh-skeleton">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-14 ios-liquid-card rounded-2xl" />
              ))}
            </div>
            <div className="flex gap-3.5 overflow-hidden">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="w-[168px] h-[220px] flex-none ios-liquid-card rounded-2xl" />
              ))}
            </div>
          </div>
        ) : !hasRenderableContent ? (
          <div className="ios-liquid-card rounded-2xl p-8 flex flex-col items-center justify-center text-center gap-3 max-w-md">
            <Compass size={28} className="text-[#6D7796]" />
            <div>
              <p className="text-sm font-semibold text-[#F1F4FA]">Unable to load Home feed</p>
              <p className="text-xs text-[#9AA5C4] mt-1">
                Check your connection or browse your Offline Downloads and Liked Songs.
              </p>
            </div>
            <button
              onClick={() => void loadFeed(activeMood, true)}
              className="px-4 py-1.5 rounded-full text-xs font-semibold bg-[#F1F4FA] text-[#06080F] hover:opacity-90 transition-opacity"
            >
              Retry
            </button>
          </div>
        ) : (
          <>
            {activeMood === 'for_you' && speedDial.length > 0 && (
              <section className="space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-[11px] font-bold uppercase tracking-[0.22em] text-[#A6B0CF]">
                    Jump Back In
                  </h2>
                  <button
                    onClick={() => onPlayAll(speedDial)}
                    className="text-xs text-[#F4F6FF] font-medium px-3 py-1 rounded-full ios-liquid-pill"
                  >
                    Play Mix
                  </button>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                  {speedDial.slice(0, 6).map((track) => {
                    const isCurrent = currentTrackId === track.video_id;
                    return (
                      <div
                        key={`dial-${track.video_id}`}
                        onClick={() => onPlayTrack(track)}
                        onMouseEnter={() => scheduleTrackPreload(track)}
                        onMouseLeave={() => cancelTrackPreload(track.video_id)}
                        onContextMenu={(e) => handleTrackContextMenu(e, track)}
                        className="group ios-liquid-card flex items-center gap-3 pr-3.5 rounded-2xl overflow-hidden cursor-pointer"
                        style={
                          isCurrent
                            ? { borderColor: 'rgba(var(--dynamic-accent-rgb), 0.65)' }
                            : undefined
                        }
                      >
                        <div className="w-14 h-14 flex-none bg-black relative overflow-hidden">
                          <TrackImage
                            src={track.thumbnail_url}
                            videoId={track.video_id}
                            size="sm"
                            iconSize={18}
                          />
                          <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
                            <Play size={16} className="fill-white text-white ml-0.5" />
                          </div>
                        </div>
                        <div className="min-w-0 flex-1 py-1.5">
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
                            className="text-[11px] text-[#A6B0CF] mt-0.5"
                          />
                        </div>
                        {(track.play_count ?? 0) > 1 && (
                          <span className="text-[10px] font-mono tabular-nums text-[#C8D1EE] bg-white/[0.08] px-2 py-0.5 rounded-full flex-none">
                            {track.play_count}x
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {sections.map((section, sIdx) => {
              const validTracks: Track[] = (section.contents || []).filter(
                (c) => c && typeof c === 'object' && c.video_id
              );
              if (validTracks.length === 0) return null;

              if (section.layout === 'compact_grid') {
                return (
                  <section
                    key={`${section.id || section.title}-${sIdx}`}
                    className="space-y-3.5 eh-shelf-section"
                  >
                    <div className="flex items-end justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          {getSectionIcon(section.title)}
                          <h2 className="text-lg font-bold tracking-tight text-[#F4F6FF]">
                            {section.title}
                          </h2>
                        </div>
                        {section.subtitle && (
                          <p className="text-[11px] text-[#A6B0CF] mt-0.5">{section.subtitle}</p>
                        )}
                      </div>
                      <button
                        onClick={() => onPlayAll(validTracks)}
                        className="text-[11px] text-[#F4F6FF] font-medium px-3 py-1 rounded-full ios-liquid-pill flex-none"
                      >
                        Play All
                      </button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
                      {validTracks.slice(0, 9).map((item, idx) => {
                        const isCurrent = currentTrackId === item.video_id;
                        const isLiked = likedIds ? likedIds.has(item.video_id) : !!item.is_liked;
                        return (
                          <div
                            key={`${item.video_id}-${idx}`}
                            onClick={() => onPlayTrack(item)}
                            onMouseEnter={() => scheduleTrackPreload(item)}
                            onMouseLeave={() => cancelTrackPreload(item.video_id)}
                            onContextMenu={(e) => handleTrackContextMenu(e, item)}
                            className="group ios-liquid-card flex items-center gap-3 p-2 rounded-2xl cursor-pointer"
                            style={
                              isCurrent
                                ? { borderColor: 'rgba(var(--dynamic-accent-rgb), 0.6)' }
                                : undefined
                            }
                          >
                            <div className="relative w-12 h-12 rounded-xl bg-black overflow-hidden flex-none border border-white/15">
                              <TrackImage
                                src={item.thumbnail_url}
                                videoId={item.video_id}
                                size="sm"
                                iconSize={16}
                              />
                              <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
                                <Play size={14} className="fill-white text-white ml-0.5" />
                              </div>
                            </div>
                            <div className="min-w-0 flex-1">
                              <div
                                className="text-xs font-semibold truncate"
                                style={
                                  isCurrent
                                    ? { color: 'var(--dynamic-accent)' }
                                    : { color: '#F4F6FF' }
                                }
                              >
                                {item.title}
                              </div>
                              <ArtistLinks
                                artist={item.artist}
                                artistId={item.artist_id}
                                artists={item.artists}
                                onOpenArtist={onOpenArtist}
                                className="text-[11px] text-[#A6B0CF] mt-0.5"
                              />
                            </div>
                            <div className="flex items-center gap-0.5 flex-none">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onAddNext(item);
                                }}
                                title="Play Next"
                                className="p-1.5 text-[#A6B0CF] hover:text-white opacity-0 group-hover:opacity-100 transition-opacity"
                              >
                                <Radio size={13} style={{ color: 'var(--dynamic-accent)' }} />
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onToggleLike(item);
                                }}
                                title={isLiked ? 'Unlike' : 'Like'}
                                className="p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] opacity-0 group-hover:opacity-100 transition-opacity"
                                style={isLiked ? { opacity: 1 } : undefined}
                              >
                                <Heart
                                  size={14}
                                  className={isLiked ? 'fill-[#FF4D8D] text-[#FF4D8D]' : ''}
                                />
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </section>
                );
              }

              return (
                <HorizontalShelf
                  key={`${section.id || section.title}-${sIdx}`}
                  title={section.title}
                  subtitle={section.subtitle}
                  icon={getSectionIcon(section.title)}
                  tracks={validTracks}
                  currentTrackId={currentTrackId}
                  likedIds={likedIds}
                  onPlayTrack={onPlayTrack}
                  onPlayAll={onPlayAll}
                  onToggleLike={onToggleLike}
                  onAddNext={onAddNext}
                  onOpenArtist={onOpenArtist}
                  onContextMenuTrack={handleTrackContextMenu}
                />
              );
            })}
          </>
        )}
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
  );
});
