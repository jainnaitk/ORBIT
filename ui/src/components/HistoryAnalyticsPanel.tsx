import { memo, useEffect, useState, useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Clock, Flame, Play, UserRound } from 'lucide-react';
import { TrackImage } from '../components/TrackImage';
import { ArtistLinks } from '../components/ArtistLinks';
import { bridgeApi } from '../api/client';
import type { ListeningStats, StatsRange, Track } from '../api/types';

interface HistoryAnalyticsPanelProps {
  libraryVersion?: number;
  onPlay: (track: Track, index: number, allTracks: Track[]) => void;
  onOpenArtist: (artistName: string, artistId?: string | null) => void;
  onStatsLoaded?: (stats: ListeningStats) => void;
}

const RANGES: { id: StatsRange; label: string }[] = [
  { id: '1w', label: '1 Week' },
  { id: '1m', label: '1 Month' },
  { id: '3m', label: '3 Months' },
  { id: '6m', label: '6 Months' },
  { id: '1y', label: '1 Year' },
  { id: 'all', label: 'All Time' },
];

export function formatListeningTime(secs: number): string {
  if (!secs || secs <= 0) return '0 min';
  const hrs = Math.floor(secs / 3600);
  const mins = Math.floor((secs % 3600) / 60);
  if (hrs > 0) return `${hrs}h ${mins}m`;
  if (mins > 0) return `${mins} min`;
  return `${Math.round(secs)}s`;
}

export const HistoryAnalyticsPanel = memo(({
  libraryVersion = 0,
  onPlay,
  onOpenArtist,
  onStatsLoaded,
}: HistoryAnalyticsPanelProps) => {
  const [range, setRange] = useState<StatsRange>('1m');
  const [stats, setStats] = useState<ListeningStats | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const topTracksRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    bridgeApi
      .getStats(range)
      .then((res) => {
        if (!cancelled) {
          setStats(res);
          onStatsLoaded?.(res);
        }
      })
      .catch(() => {
        if (!cancelled) {
          const empty: ListeningStats = {
            range,
            total_plays: 0,
            total_seconds: 0,
            top_tracks: [],
            top_artists: [],
          };
          setStats(empty);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range, libraryVersion, onStatsLoaded]);

  const topTracks = stats?.top_tracks || [];
  const topArtists = stats?.top_artists || [];
  const topArtistName = topArtists[0]?.name || topArtists[0]?.artist || 'No plays yet';
  const maxTrackPlays = Math.max(1, ...topTracks.map((t) => t.plays));
  const maxArtistPlays = Math.max(1, ...topArtists.map((a) => a.plays));

  const rowVirtualizer = useVirtualizer({
    count: topTracks.length,
    getScrollElement: () => topTracksRef.current,
    estimateSize: () => 58,
    overscan: 6,
  });

  return (
    <div className="analytics-panel flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
      <div className="flex-none px-4 sm:px-5 py-2.5 border-b border-white/[0.08] bg-white/[0.015] flex items-center justify-between gap-2 flex-wrap">
        <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#9AA5C4]">
          Listening Analytics
        </span>
        <div className="flex items-center max-w-full overflow-x-auto whitespace-nowrap gap-1 p-0.5 rounded-full bg-white/[0.05] border border-white/[0.09]">
          {RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setRange(r.id)}
              className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors ${
                range === r.id
                  ? 'bg-[#F4F6FF] text-[#020308] font-semibold'
                  : 'text-[#A6B0CF] hover:text-white'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-5">
        {/* Summary Cards */}
        <div className="stats-summary grid gap-3">
          <div className="ios-liquid-card rounded-2xl p-4 space-y-1">
            <div className="flex items-center gap-2 text-[11px] text-[#8E99BA] uppercase tracking-wider">
              <Flame size={13} style={{ color: 'var(--dynamic-accent)' }} />
              <span>Qualified Plays</span>
            </div>
            <div className="text-2xl font-bold text-[#F4F6FF] font-mono tabular-nums">
              {loading && !stats ? '—' : stats?.total_plays ?? 0}
            </div>
          </div>
          <div className="ios-liquid-card rounded-2xl p-4 space-y-1">
            <div className="flex items-center gap-2 text-[11px] text-[#8E99BA] uppercase tracking-wider">
              <Clock size={13} className="text-[#34D399]" />
              <span>Listening Time</span>
            </div>
            <div className="text-2xl font-bold text-[#F4F6FF] font-mono tabular-nums">
              {loading && !stats ? '—' : formatListeningTime(stats?.total_seconds ?? 0)}
            </div>
          </div>
          <div className="ios-liquid-card rounded-2xl p-4 space-y-1">
            <div className="flex items-center gap-2 text-[11px] text-[#8E99BA] uppercase tracking-wider">
              <UserRound size={13} className="text-[#F59E0B]" />
              <span>Top Artist</span>
            </div>
            <div className="text-lg font-bold text-[#F4F6FF] truncate">
              {loading && !stats ? '—' : topArtistName}
            </div>
          </div>
        </div>

        {/* Two Columns: Top Tracks + Top Artists */}
        <div className="stats-rankings grid gap-4">
          <section className="min-w-0 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4 flex flex-col">
            <h2 className="text-xs font-bold uppercase tracking-[0.18em] text-[#A6B0CF] mb-3">
              Top Tracks ({topTracks.length})
            </h2>
            {topTracks.length === 0 ? (
              <div className="py-12 text-center text-xs text-[#8E99BA]">
                Listen past 30s on tracks to populate qualified listening stats.
              </div>
            ) : (
              <div
                ref={topTracksRef}
                style={{ contain: 'strict' }}
                className="h-[340px] overflow-y-auto relative"
              >
                <div
                  style={{
                    height: `${rowVirtualizer.getTotalSize()}px`,
                    width: '100%',
                    position: 'relative',
                  }}
                >
                  {rowVirtualizer.getVirtualItems().map((vRow) => {
                    const item = topTracks[vRow.index];
                    if (!item) return null;
                    const pct = Math.max(0.06, item.plays / maxTrackPlays);
                    const secs = item.listened_s ?? item.seconds ?? 0;
                    return (
                      <div
                        key={`${item.video_id}-${vRow.index}`}
                        onClick={() => onPlay(item, vRow.index, topTracks)}
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          height: '58px',
                          transform: `translate3d(0, ${vRow.start}px, 0)`,
                        }}
                        className="flex items-center gap-3 px-2.5 rounded-xl hover:bg-white/[0.06] group cursor-pointer"
                      >
                        <span className="w-5 text-center text-xs font-mono text-[#7A84A6]">
                          {vRow.index + 1}
                        </span>
                        <div className="relative w-9 h-9 rounded-lg bg-black overflow-hidden flex-none border border-white/10">
                          <TrackImage src={item.thumbnail_url} videoId={item.video_id} size="sm" iconSize={14} />
                          <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 flex items-center justify-center">
                            <Play size={11} className="fill-white text-white ml-0.5" />
                          </div>
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="text-xs font-semibold text-[#F4F6FF] truncate">
                            {item.title}
                          </div>
                          <ArtistLinks
                            artist={item.artist}
                            artistId={item.artist_id}
                            artists={item.artists}
                            onOpenArtist={onOpenArtist}
                            className="text-[10.5px] text-[#8E99BA]"
                          />
                          <div className="mt-1 h-1 rounded-full bg-white/[0.06] overflow-hidden">
                            <div
                              className="h-full rounded-full origin-left"
                              style={{
                                background: 'var(--dynamic-accent)',
                                transform: `scaleX(${pct})`,
                              }}
                            />
                          </div>
                        </div>
                        <div className="text-right flex-none">
                          <div className="text-xs font-mono font-semibold text-[#E6ECF8]">
                            {item.plays}
                          </div>
                          <div className="text-[10px] text-[#7A84A6]">
                            {formatListeningTime(secs)}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </section>

          <section className="min-w-0 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4">
            <h2 className="text-xs font-bold uppercase tracking-[0.18em] text-[#A6B0CF] mb-3">
              Top Artists ({topArtists.length})
            </h2>
            {topArtists.length === 0 ? (
              <div className="py-12 text-center text-xs text-[#8E99BA]">No artist stats yet.</div>
            ) : (
              <div className="space-y-2.5 max-h-[340px] overflow-y-auto pr-1">
                {topArtists.slice(0, 15).map((art, idx) => {
                  const pct = Math.max(0.08, art.plays / maxArtistPlays);
                  const displayName = art.name || art.artist || 'Unknown Artist';
                  const secs = art.listened_s ?? art.seconds ?? 0;
                  return (
                    <button
                      key={`${art.artist_norm || displayName}-${idx}`}
                      type="button"
                      onClick={() => onOpenArtist(displayName)}
                      className="w-full flex items-center gap-3 p-2 rounded-xl hover:bg-white/[0.06] text-left transition-colors"
                    >
                      <span className="w-4 text-xs font-mono text-[#7A84A6]">{idx + 1}</span>
                      <div className="w-9 h-9 rounded-full bg-[#06080F] border border-white/15 overflow-hidden flex items-center justify-center flex-none">
                        {art.thumbnail_url ? (
                          <TrackImage src={art.thumbnail_url} size="sm" iconSize={14} />
                        ) : (
                          <UserRound size={14} className="text-[#8E99BA]" />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-semibold text-[#F4F6FF] truncate">
                          {displayName}
                        </div>
                        <div className="mt-1 h-1 rounded-full bg-white/[0.06] overflow-hidden">
                          <div
                            className="h-full rounded-full bg-[#34D399] origin-left"
                            style={{ transform: `scaleX(${pct})` }}
                          />
                        </div>
                      </div>
                      <div className="text-right flex-none">
                        <div className="text-xs font-mono font-semibold text-[#E6ECF8]">
                          {art.plays}
                        </div>
                        <div className="text-[10px] text-[#7A84A6]">
                          {formatListeningTime(secs)}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
});
