import { useEffect, useState } from 'react';
import { TrackImage } from './TrackImage';
import type { Track } from '../api/types';

export interface SuckAnimationPayload {
  id: number;
  track: Track;
}

interface BlackHoleTransitionProps {
  payload: SuckAnimationPayload | null;
  enabled?: boolean;
}

export const BlackHoleTransition = ({ payload, enabled = true }: BlackHoleTransitionProps) => {
  const [visiblePayload, setVisiblePayload] = useState<SuckAnimationPayload | null>(null);

  useEffect(() => {
    if (!enabled || !payload) {
      setVisiblePayload(null);
      return;
    }
    setVisiblePayload(payload);
    const timer = window.setTimeout(() => {
      setVisiblePayload((curr) => (curr?.id === payload.id ? null : curr));
    }, 1080);
    return () => clearTimeout(timer);
  }, [payload, enabled]);

  if (!enabled || !visiblePayload) return null;

  return (
    <div
      key={visiblePayload.id}
      className="pointer-events-none fixed bottom-6 left-10 z-[80] eh-suck-card"
    >
      <div
        className="flex items-center gap-3 px-3 py-2 rounded-2xl ios-liquid-glass w-60"
        style={{
          borderColor: 'rgba(var(--dynamic-accent-rgb), 0.38)',
          boxShadow: '0 10px 26px rgba(0, 0, 0, 0.65), 0 0 14px rgba(var(--dynamic-accent-rgb), 0.16)',
        }}
      >
        <div className="w-11 h-11 rounded-full overflow-hidden border border-white/15 flex-none bg-[#06080F]">
          <TrackImage
            src={visiblePayload.track.thumbnail_url}
            videoId={visiblePayload.track.video_id}
            size="sm"
            iconSize={16}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold text-[#F1F4FA] truncate">
            {visiblePayload.track.title}
          </div>
          <div className="text-[10px] text-[#9AA5C4] truncate">
            {visiblePayload.track.artist}
          </div>
        </div>
      </div>
    </div>
  );
};
