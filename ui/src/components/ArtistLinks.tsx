import { Fragment, memo } from 'react';
import type { ArtistCredit } from '../api/types';
import { parseTrackArtists } from '../utils/artists';

interface ArtistLinksProps {
  artist?: string | null;
  artistId?: string | null;
  artists?: ArtistCredit[] | null;
  onOpenArtist?: (artistName: string, artistId?: string | null) => void;
  className?: string;
  buttonClassName?: string;
  fallback?: string;
  stopPropagation?: boolean;
  inline?: boolean;
  onAfterSelect?: () => void;
}

export const ArtistLinks = memo(({
  artist,
  artistId,
  artists,
  onOpenArtist,
  className = '',
  buttonClassName = 'hover:text-white hover:underline',
  fallback = 'Unknown Artist',
  stopPropagation = true,
  inline = false,
  onAfterSelect,
}: ArtistLinksProps) => {
  const parsed = parseTrackArtists(artist, artistId, artists);
  const displayFallback = artist || fallback;

  if (!onOpenArtist || parsed.length === 0) {
    if (inline) {
      return <span className={className}>{displayFallback}</span>;
    }
    return <div className={`truncate ${className}`}>{displayFallback}</div>;
  }

  if (parsed.length === 1) {
    const only = parsed[0];
    return (
      <button
        type="button"
        onClick={(e) => {
          if (stopPropagation) e.stopPropagation();
          onAfterSelect?.();
          onOpenArtist(only.name, only.channelId);
        }}
        title={`Go to ${only.name}`}
        className={
          inline
            ? `inline text-left cursor-pointer transition-colors ${buttonClassName} ${className}`
            : `block max-w-full text-left truncate cursor-pointer transition-colors ${buttonClassName} ${className}`
        }
      >
        {only.name}
      </button>
    );
  }

  return (
    <span
      className={
        inline
          ? className
          : `block max-w-full truncate ${className}`
      }
      title={parsed.map((p) => p.name).join(', ')}
    >
      {parsed.map((item, idx) => (
        <Fragment key={`${item.name}-${idx}`}>
          {idx > 0 && <span className="pointer-events-none">, </span>}
          <button
            type="button"
            onClick={(e) => {
              if (stopPropagation) e.stopPropagation();
              onAfterSelect?.();
              onOpenArtist(item.name, item.channelId);
            }}
            title={`Go to ${item.name}`}
            className={`inline text-left cursor-pointer transition-colors ${buttonClassName}`}
          >
            {item.name}
          </button>
        </Fragment>
      ))}
    </span>
  );
});
