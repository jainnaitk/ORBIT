import { useEffect, useState } from 'react';
import { Play, Sparkles, Flame, Clock, Music } from 'lucide-react';
import type { Track, HomeSection } from '../api/types';
import { bridgeApi } from '../api/client';

interface HomeViewProps {
  onPlayTrack: (track: Track) => void;
  onPlayAll: (tracks: Track[]) => void;
}

export const HomeView = ({ onPlayTrack, onPlayAll }: HomeViewProps) => {
  const [sections, setSections] = useState<HomeSection[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  useEffect(() => {
    let active = true;
    const loadHome = async () => {
      setIsLoading(true);
      try {
        const data = await bridgeApi.getHome();
        if (active && Array.isArray(data)) {
          setSections(data);
        }
      } catch {
        // Fallback or offline state
      } finally {
        if (active) setIsLoading(false);
      }
    };
    loadHome();
    return () => {
      active = false;
    };
  }, []);

  const formatThumb = (url?: string | null): string | undefined => {
    if (!url) return undefined;
    if (url.includes('googleusercontent.com') || url.includes('ggpht.com')) {
      return url.replace(/=w\d+-h\d+/, '=w180-h180');
    }
    return url;
  };

  const getSectionIcon = (title: string) => {
    const lower = title.toLowerCase();
    if (lower.includes('quick') || lower.includes('pick')) {
      return <Sparkles size={16} className="text-accent" />;
    }
    if (lower.includes('trend') || lower.includes('top')) {
      return <Flame size={16} className="text-rose-400" />;
    }
    return <Clock size={16} className="text-emerald-400" />;
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-8 select-none">
      {/* Hero Welcome banner */}
      <div className="rounded-xl bg-gradient-to-r from-bg-surface to-bg-hover/60 border border-border-subtle p-6 flex items-center justify-between">
        <div>
          <span className="text-xs uppercase tracking-wider text-accent font-semibold">
            YouTube Music • Local Bridge
          </span>
          <h1 className="text-2xl font-bold text-text-1 mt-1">Welcome to MetroLite</h1>
          <p className="text-xs text-text-2 mt-1">
            Zero-jank, gapless desktop audio with lightweight native background playback.
          </p>
        </div>
      </div>

      {isLoading && sections.length === 0 ? (
        <div className="space-y-6 animate-pulse">
          <div className="h-4 bg-bg-surface rounded w-48 mb-3"></div>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-44 bg-bg-surface rounded-lg"></div>
            ))}
          </div>
        </div>
      ) : (
        sections.map((section, sIdx) => {
          const validTracks: Track[] = (section.contents || []).filter(
            (c) => c && typeof c === 'object' && c.video_id
          );

          if (validTracks.length === 0) return null;

          return (
            <section key={section.title || sIdx} className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {getSectionIcon(section.title)}
                  <h2 className="text-base font-bold text-text-1">{section.title}</h2>
                </div>
                {validTracks.length > 0 && (
                  <button
                    onClick={() => onPlayAll(validTracks)}
                    className="text-xs text-accent hover:underline flex items-center gap-1 font-medium"
                  >
                    Play All
                  </button>
                )}
              </div>

              {/* Horizontal Scroll Shelf */}
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3.5">
                {validTracks.slice(0, 12).map((item) => (
                  <div
                    key={item.video_id}
                    onClick={() => onPlayTrack(item)}
                    className="group bg-bg-surface hover:bg-bg-hover border border-border-subtle hover:border-border-light rounded-lg p-2.5 transition-all cursor-pointer flex flex-col"
                  >
                    <div className="relative aspect-square w-full rounded-md bg-bg-base overflow-hidden border border-border-subtle mb-2.5 flex items-center justify-center">
                      {item.thumbnail_url ? (
                        <img
                          src={formatThumb(item.thumbnail_url)}
                          alt=""
                          loading="lazy"
                          decoding="async"
                          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                        />
                      ) : (
                        <Music size={24} className="text-text-2 opacity-50" />
                      )}

                      {/* Play overlay circle */}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onPlayTrack(item);
                        }}
                        className="absolute bottom-2 right-2 w-8 h-8 rounded-full bg-accent text-bg-base flex items-center justify-center shadow-lg opacity-0 group-hover:opacity-100 group-hover:translate-y-0 translate-y-1 transition-all"
                      >
                        <Play size={14} className="fill-current ml-0.5" />
                      </button>
                    </div>

                    <div className="text-xs font-semibold text-text-1 truncate group-hover:text-accent transition-colors">
                      {item.title}
                    </div>
                    <div className="text-[11px] text-text-2 truncate mt-0.5">
                      {item.artist}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          );
        })
      )}
    </div>
  );
};
