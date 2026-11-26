import type { ArtistCredit, Track } from '../api/types';

export interface ArtistRef {
  name: string;
  channelId?: string | null;
}

const COMMA_ARTIST_EXCEPTIONS: RegExp[] = [
  /Tyler,\s*The Creator/gi,
  /Earth,\s*Wind\s*&\s*Fire/gi,
  /Crosby,\s*Stills\s*(?:,\s*Nash(?:\s*&\s*Young)?)?/gi,
  /Emerson,\s*Lake\s*&\s*Palmer/gi,
  /Blood,\s*Sweat\s*&\s*Tears/gi,
  /Peter,\s*Paul\s*(?:and|&)\s*Mary/gi,
];

/**
 * Split a track's artist credit string (or structured credits) into individual clickable artists.
 * Assigns `primaryArtistId` only to the primary (first) artist when structured per-artist IDs
 * are absent so secondary/featured artists resolve via their own name lookup.
 */
export function parseTrackArtists(
  artistStr?: string | null,
  primaryArtistId?: string | null,
  credits?: ArtistCredit[] | null
): ArtistRef[] {
  if (Array.isArray(credits) && credits.length > 0) {
    const out: ArtistRef[] = [];
    const seen = new Set<string>();
    credits.forEach((credit, idx) => {
      const name = (credit?.name || '').trim();
      if (!name) return;
      const key = name.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push({
        name,
        channelId: credit.id || (idx === 0 ? primaryArtistId || null : null),
      });
    });
    if (out.length > 0) return out;
  }

  const raw = (artistStr || '').trim();
  if (!raw) return [];

  const placeholders: string[] = [];
  let masked = raw;
  for (const rx of COMMA_ARTIST_EXCEPTIONS) {
    masked = masked.replace(rx, (match) => {
      const token = `__ORBIT_ARTIST_${placeholders.length}__`;
      placeholders.push(match);
      return token;
    });
  }

  const normalized = masked.replace(/\s+(?:feat\.?|ft\.?|featuring)\s+/gi, ', ');
  const parts = normalized
    .split(/\s*(?:,|•)\s*/)
    .map((segment) => {
      let restored = segment.trim();
      placeholders.forEach((orig, idx) => {
        restored = restored.replace(`__ORBIT_ARTIST_${idx}__`, orig);
      });
      return restored;
    })
    .filter(Boolean);

  const out: ArtistRef[] = [];
  const seen = new Set<string>();
  parts.forEach((name, idx) => {
    const key = name.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push({
      name,
      channelId: idx === 0 ? primaryArtistId || null : null,
    });
  });

  return out;
}

export function getTrackArtists(
  track?: Pick<Track, 'artist' | 'artist_id' | 'artists'> | null
): ArtistRef[] {
  if (!track) return [];
  return parseTrackArtists(track.artist, track.artist_id, track.artists);
}
