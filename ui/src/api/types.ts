/**
 * ORBIT typed API and WebSocket message specifications.
 */

export interface ArtistCredit {
  name: string;
  id?: string | null;
}

export interface Track {
  video_id: string;
  title: string;
  artist: string;
  artist_id?: string | null;
  artists?: ArtistCredit[];
  album?: string | null;
  album_id?: string | null;
  duration_s: number;
  thumbnail_url?: string | null;
  is_liked?: boolean;
  local_path?: string | null;
  download_status?: 'none' | 'queued' | 'downloading' | 'completed' | 'failed';
  play_count?: number;
  last_played_at?: string | null;
}

export interface ArtistRelease {
  browse_id: string;
  title: string;
  year?: string | null;
  type?: string;
  thumbnail_url?: string | null;
  artist?: string;
  artist_id?: string | null;
}

export interface ArtistProfile {
  channel_id?: string | null;
  name: string;
  description?: string | null;
  subscribers?: string | null;
  views?: string | null;
  thumbnail_url?: string | null;
  popular_tracks: Track[];
  all_tracks: Track[];
  albums: ArtistRelease[];
  singles: ArtistRelease[];
}

export interface AlbumDetails {
  browse_id?: string | null;
  title: string;
  artist: string;
  artist_id?: string | null;
  artists?: ArtistCredit[];
  year?: string | null;
  type?: string;
  description?: string | null;
  thumbnail_url?: string | null;
  tracks: Track[];
}

export type PlaybackState = 'idle' | 'playing' | 'paused' | 'buffering' | 'stopped';

export interface PlaybackTick {
  time_pos: number;
  duration: number;
  state: PlaybackState;
  volume: number;
  current_track: Track | null;
}

export interface TrackChangedEvent {
  track?: Track | null;
  current_track?: Track | null;
  time_pos?: number;
  duration?: number;
  state?: PlaybackState;
  index?: number;
  queue_index?: number;
}

export type RepeatMode = 'off' | 'all' | 'one';

export type EqPreset = 'flat' | 'bass' | 'vocal' | 'night' | 'studio';

export interface LyricWord {
  time: number;
  end: number;
  text: string;
}

export interface LyricLine {
  time: number;
  end?: number;
  text: string;
  romanized?: string;
  type?: 'line' | 'instrumental';
  align?: 'left' | 'right';
  duration?: number;
  words?: LyricWord[];
}

export type LyricsSyncType = 'word' | 'line' | 'plain';

export interface LyricVariant {
  code: string;
  label: string;
  script: 'latin' | 'native';
  synced: LyricLine[] | null;
  lyrics: string | null;
  sync_type?: LyricsSyncType;
  source?: string | null;
}

export interface LyricsResponse {
  synced: LyricLine[] | null;
  lyrics: string | null;
  source: string | null;
  sync_type?: LyricsSyncType;
  credit?: string | null;
  credit_url?: string | null;
  submitter?: string | null;
  submitter_url?: string | null;
  songwriters?: string[];
  original_lang?: string;
  variants?: LyricVariant[];
  offline?: boolean;
}

export interface QueueData {
  current_index: number;
  items: Track[];
  shuffle: boolean;
  repeat_mode: RepeatMode;
  autoplay_radio: boolean;
  current_track: Track | null;
}

export interface DownloadProgress {
  video_id: string;
  status: 'queued' | 'downloading' | 'tagging' | 'completed' | 'failed' | 'error' | 'deleted';
  percent?: number;
  progress_pct?: number;
  downloaded_bytes?: number;
  total_bytes?: number;
  local_path?: string | null;
  error?: string | null;
}

export interface LibraryChangedEvent {
  type: string;
  action?: string;
  item_type?: string;
  video_id?: string;
  is_liked?: boolean;
  id?: string;
  playlist?: any;
}

export interface Playlist {
  id: string;
  name: string;
  description: string;
  created_at?: string;
  updated_at?: string;
  track_count?: number;
  total_duration_s?: number;
}

export interface HomeSection {
  id?: string;
  title: string;
  subtitle?: string;
  layout?: 'compact_grid' | 'cards';
  contents: (Track | any)[];
}

export interface PersonalizedHomeResponse {
  greeting: string;
  speed_dial: Track[];
  sections: HomeSection[];
  personalized: boolean;
}

export interface BridgeConfig {
  host: string;
  port: number;
  token: string;
}

export interface BridgeErrorEvent {
  message: string;
  video_id?: string;
  consecutive_errors?: number;
}

export type UpdateStatusKind =
  | 'idle'
  | 'checking'
  | 'up_to_date'
  | 'available'
  | 'downloading'
  | 'ready'
  | 'launched'
  | 'error';

export interface UpdateState {
  status: UpdateStatusKind;
  repo: string;
  current_version: string;
  latest_version: string;
  update_available: boolean;
  release_name?: string | null;
  release_notes?: string | null;
  changelog?: string[];
  published_at?: string | null;
  html_url?: string | null;
  asset_name?: string | null;
  asset_size?: number;
  download_url?: string | null;
  downloaded_path?: string | null;
  downloaded_bytes?: number;
  total_bytes?: number;
  progress_pct?: number;
  error?: string | null;
}

export type BridgeEventMap = {
  playback_tick: PlaybackTick;
  track_changed: TrackChangedEvent;
  queue_changed: QueueData;
  download_progress: DownloadProgress;
  library_changed: LibraryChangedEvent;
  update_status: UpdateState;
  error: BridgeErrorEvent;
};
