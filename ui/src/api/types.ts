/**
 * MetroLite typed API and WebSocket message specifications.
 */

export interface Track {
  video_id: string;
  title: string;
  artist: string;
  album?: string | null;
  duration_s: number;
  thumbnail_url?: string | null;
  is_liked?: boolean;
  local_path?: string | null;
  download_status?: 'none' | 'queued' | 'downloading' | 'completed' | 'failed';
  play_count?: number;
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
  current_track: Track | null;
  time_pos: number;
  duration: number;
  state: PlaybackState;
  queue_index: number;
}

export type RepeatMode = 'off' | 'all' | 'one';

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
  status: 'queued' | 'downloading' | 'completed' | 'failed';
  progress_pct: number;
  downloaded_bytes: number;
  total_bytes: number;
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
  title: string;
  contents: (Track | any)[];
}

export interface BridgeConfig {
  host: string;
  port: number;
  token: string;
}

export type BridgeEventMap = {
  playback_tick: PlaybackTick;
  track_changed: TrackChangedEvent;
  queue_changed: QueueData;
  download_progress: DownloadProgress;
  library_changed: LibraryChangedEvent;
};
