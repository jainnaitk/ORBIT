import { useState, useEffect, useRef } from 'react';
import { Music } from 'lucide-react';
import { bridgeApi } from '../api/client';

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

// Bounded set of URLs already decoded by WebView2 so tab switches & Dynamic Island morphs
// render cached artwork on Frame 1 with zero blank/skeleton flicker.
const __LOADED_IMAGE_URLS__ = new Set<string>();
const MAX_LOADED_URL_CACHE = 320;

function markUrlLoaded(url: string): void {
  if (__LOADED_IMAGE_URLS__.has(url)) return;
  if (__LOADED_IMAGE_URLS__.size >= MAX_LOADED_URL_CACHE) {
    const oldest = __LOADED_IMAGE_URLS__.values().next().value;
    if (oldest) __LOADED_IMAGE_URLS__.delete(oldest);
  }
  __LOADED_IMAGE_URLS__.add(url);
}

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

function buildCandidates(
  src: string | null | undefined,
  videoId: string | null | undefined,
  size: 'sm' | 'md' | 'lg'
): string[] {
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
  return candidates;
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
  const candidates = buildCandidates(src, videoId, size);
  const [isOffline, setIsOffline] = useState<boolean>(
    () => typeof navigator !== 'undefined' && navigator.onLine === false
  );
  const key = `${src || ''}|${videoId || ''}|${size}`;
  const [stageState, setStageState] = useState<{ key: string; stage: number }>({ key, stage: 0 });
  const [localArt, setLocalArt] = useState<{ videoId: string; url: string | null }>({
    videoId: videoId || '',
    url: videoId ? bridgeApi.getCachedLocalArtworkUrl(videoId) : null,
  });

  useEffect(() => {
    const onOnline = () => {
      setIsOffline(false);
      setStageState((prev) => ({ key: prev.key, stage: 0 }));
    };
    const onOffline = () => setIsOffline(true);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    return () => {
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
    };
  }, []);

  const stage = stageState.key === key ? stageState.stage : 0;
  const remoteUrl = isOffline ? undefined : candidates[stage];
  const needsLocalFallback = Boolean(videoId) && (isOffline || !remoteUrl);

  useEffect(() => {
    if (!needsLocalFallback || !videoId) return;
    const cached = bridgeApi.getCachedLocalArtworkUrl(videoId);
    if (cached) {
      setLocalArt({ videoId, url: cached });
      return;
    }
    let cancelled = false;
    void bridgeApi.getLocalArtworkUrl(videoId).then((resolved) => {
      if (!cancelled) {
        setLocalArt({ videoId, url: resolved });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [needsLocalFallback, videoId]);

  const localUrl =
    videoId && localArt.videoId === videoId
      ? localArt.url
      : videoId
      ? bridgeApi.getCachedLocalArtworkUrl(videoId)
      : null;

  const activeUrl = remoteUrl || localUrl || undefined;
  const usingLocalBlob = Boolean(!remoteUrl && localUrl);

  const isCached = Boolean(
    activeUrl && (usingLocalBlob || __LOADED_IMAGE_URLS__.has(activeUrl))
  );
  const [loadedUrl, setLoadedUrl] = useState<string | null>(isCached ? activeUrl || null : null);
  const imgRef = useRef<HTMLImageElement>(null);

  const isLoaded = isCached || (Boolean(activeUrl) && loadedUrl === activeUrl);

  useEffect(() => {
    const img = imgRef.current;
    if (activeUrl && img && img.complete && img.naturalWidth > 0) {
      markUrlLoaded(activeUrl);
      setLoadedUrl(activeUrl);
    }
  }, [activeUrl]);

  if (!activeUrl) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-[#0D1220] text-[#7A84A6]/60">
        <Music size={iconSize} />
      </div>
    );
  }

  return (
    <div className="relative w-full h-full overflow-hidden bg-[#0C101C]">
      {!isLoaded && (
        <div
          className="absolute inset-0 eh-skeleton"
          style={{
            background:
              'linear-gradient(135deg, rgba(var(--dynamic-accent-rgb), 0.14), rgba(14, 18, 30, 0.9))',
          }}
        />
      )}
      <img
        ref={imgRef}
        src={activeUrl}
        alt={alt}
        loading={eager || isCached ? 'eager' : 'lazy'}
        decoding={isCached ? 'sync' : 'async'}
        draggable={false}
        referrerPolicy="no-referrer"
        onLoad={() => {
          if (!usingLocalBlob) {
            markUrlLoaded(activeUrl);
          }
          setLoadedUrl(activeUrl);
        }}
        onError={() => {
          if (remoteUrl) {
            setStageState({ key, stage: stage + 1 });
          } else if (videoId) {
            bridgeApi.invalidateLocalArtwork(videoId);
            setLocalArt({ videoId, url: null });
          }
        }}
        className={`${className} ${isCached ? '' : 'transition-opacity duration-150'} ${
          isLoaded ? 'opacity-100' : 'opacity-0'
        }`}
      />
    </div>
  );
};
