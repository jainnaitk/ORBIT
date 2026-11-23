import { useState, useEffect } from 'react';
import { Music } from 'lucide-react';

interface TrackImageProps {
  src?: string | null;
  videoId?: string | null;
  alt?: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  iconSize?: number;
  eager?: boolean;
}

// Memory-lean decoded bitmap sizes (still crisp at 1.5x-2x DPI while saving ~50% RGBA VRAM)
const SIZE_MAP: Record<'sm' | 'md' | 'lg', string> = {
  sm: '=w96-h96',
  md: '=w180-h180',
  lg: '=w360-h360',
};

function normalizeThumb(url: string | null | undefined, size: 'sm' | 'md' | 'lg'): string | null {
  if (!url) return null;
  const trimmed = url.trim();
  if (!trimmed) return null;
  if (trimmed.includes('googleusercontent.com') || trimmed.includes('ggpht.com')) {
    if (/=w\d+-h\d+/.test(trimmed)) {
      return trimmed.replace(/=w\d+-h\d+/, SIZE_MAP[size]);
    }
  }
  return trimmed;
}

export const TrackImage = ({
  src,
  videoId,
  alt = '',
  size = 'md',
  className = 'w-full h-full object-cover',
  iconSize = 18,
  eager = false,
}: TrackImageProps) => {
  const [stage, setStage] = useState<number>(0);
  const [loaded, setLoaded] = useState<boolean>(false);

  useEffect(() => {
    setStage(0);
    setLoaded(false);
  }, [src, videoId, size]);

  const candidates: string[] = [];
  const normalized = normalizeThumb(src, size);
  if (normalized) candidates.push(normalized);
  if (src && src.trim() && src.trim() !== normalized) {
    candidates.push(src.trim());
  }
  if (videoId) {
    candidates.push(`https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/mqdefault.jpg`);
    candidates.push(`https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/hqdefault.jpg`);
  }

  const activeUrl = candidates[stage];

  if (!activeUrl) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-[#0D1220] text-[#7A84A6]/60">
        <Music size={iconSize} />
      </div>
    );
  }

  return (
    <div className="relative w-full h-full overflow-hidden bg-[#0C101C]">
      {!loaded && (
        <div
          className="absolute inset-0 eh-skeleton"
          style={{
            background:
              'linear-gradient(135deg, rgba(var(--dynamic-accent-rgb), 0.14), rgba(14, 18, 30, 0.9))',
          }}
        />
      )}
      <img
        src={activeUrl}
        alt={alt}
        loading={eager ? 'eager' : 'lazy'}
        decoding="async"
        draggable={false}
        referrerPolicy="no-referrer"
        onLoad={() => setLoaded(true)}
        onError={() => setStage((s) => s + 1)}
        className={`${className} transition-opacity duration-200 ${loaded ? 'opacity-100' : 'opacity-0'}`}
      />
    </div>
  );
};
