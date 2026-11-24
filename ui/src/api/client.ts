/**
 * Typed REST, WebSocket & Tauri Window client for ORBIT Bridge.
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import type {
  AlbumDetails,
  ArtistProfile,
  BridgeConfig,
  BridgeEventMap,
  DownloadProgress,
  EqPreset,
  HomeSection,
  LyricsResponse,
  PersonalizedHomeResponse,
  PlaybackTick,
  Playlist,
  QueueData,
  Track,
  UpdateState,
} from './types';

export const isTauri = (): boolean => {
  return typeof window !== 'undefined' && ('__TAURI_INTERNALS__' in window || '__TAURI__' in window);
};

let cachedConfig: BridgeConfig | null = null;

export async function fetchBridgeConfig(forceRefresh: boolean = false): Promise<BridgeConfig> {
  if (!forceRefresh && cachedConfig && cachedConfig.token) return cachedConfig;

  if (isTauri()) {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const cfg = await invoke<BridgeConfig>('get_bridge_config');
      if (cfg && cfg.token) {
        cachedConfig = cfg;
      }
      return cfg;
    } catch {
      // Fallback
    }
  }

  try {
    const res = await fetch('/__bridge_config', { cache: 'no-store' });
    if (res.ok) {
      const cfg = (await res.json()) as BridgeConfig;
      if (cfg && cfg.token) {
        cachedConfig = cfg;
      }
      return cfg;
    }
  } catch {
    // Ignore
  }

  const fallbackHost =
    typeof window !== 'undefined' && window.location.hostname
      ? window.location.hostname
      : '127.0.0.1';
  // Do NOT cache empty-token fallback so subsequent retries pick up bridge.json once ready
  return { host: fallbackHost, port: 8766, token: '' };
}

export async function minimizeWindow(): Promise<void> {
  if (isTauri()) {
    try {
      const { getCurrentWindow } = await import('@tauri-apps/api/window');
      await getCurrentWindow().minimize();
    } catch {
      // Ignore
    }
  }
}

export async function toggleMaximizeWindow(): Promise<void> {
  if (isTauri()) {
    try {
      const { getCurrentWindow } = await import('@tauri-apps/api/window');
      await getCurrentWindow().toggleMaximize();
    } catch {
      // Ignore
    }
  }
}

export async function closeWindow(): Promise<void> {
  if (isTauri()) {
    try {
      const { getCurrentWindow } = await import('@tauri-apps/api/window');
      await getCurrentWindow().close();
    } catch {
      // Ignore
    }
  }
}

export interface SavedWindowBounds {
  width: number;
  height: number;
  x: number;
  y: number;
  wasMaximized: boolean;
}

export function waitForNextPaint(): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    let raf1 = 0;
    let raf2 = 0;
    let timer = 0;

    const done = () => {
      if (settled) return;
      settled = true;
      if (raf1) cancelAnimationFrame(raf1);
      if (raf2) cancelAnimationFrame(raf2);
      if (timer) clearTimeout(timer);
      resolve();
    };

    // Fallback timeout guarantees hidden Win32 WebView2 windows never stall rAF indefinitely
    timer = window.setTimeout(done, 50);
    raf1 = requestAnimationFrame(() => {
      raf1 = 0;
      raf2 = requestAnimationFrame(() => {
        raf2 = 0;
        done();
      });
    });
  });
}

export async function showMainWindow(focus: boolean = true): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('show_main_window', { focus });
  } catch {
    // Fallback
  }
}

export async function trimProcessMemory(): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('trim_process_memory');
  } catch {
    // Ignore
  }
}

export async function enterDynamicIslandWindow(): Promise<SavedWindowBounds | null> {
  if (!isTauri()) return null;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    return await invoke<SavedWindowBounds>('enter_dynamic_island');
  } catch (err) {
    console.error('enterDynamicIslandWindow failed:', err);
    return null;
  }
}

export type IslandWindowMode = 'pill' | 'circle' | 'card';

export async function resizeDynamicIslandWindow(
  modeOrExpanded: IslandWindowMode | boolean
): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    const mode: IslandWindowMode =
      typeof modeOrExpanded === 'boolean' ? (modeOrExpanded ? 'pill' : 'circle') : modeOrExpanded;
    await invoke('resize_dynamic_island', { expanded: mode !== 'circle', mode });
  } catch {
    // Ignore
  }
}

export async function setDynamicIslandPinned(pinned: boolean): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('set_island_pinned', { pinned });
  } catch {
    // Ignore
  }
}

export async function exitDynamicIslandWindow(saved: SavedWindowBounds | null): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('exit_dynamic_island', { saved });
  } catch (err) {
    console.error('exitDynamicIslandWindow failed:', err);
  }
}

export async function launchUpdateInstallerAndQuit(installerPath: string): Promise<boolean> {
  if (!isTauri() || !installerPath) return false;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('launch_update_installer', { installerPath });
    return true;
  } catch (err) {
    console.error('launchUpdateInstallerAndQuit failed:', err);
    return false;
  }
}

export class BridgeApiClient {
  private config: BridgeConfig | null = null;
  private preloadedIds: Set<string> = new Set();

  constructor(config?: BridgeConfig) {
    if (config) this.config = config;
  }

  public setConfig(config: BridgeConfig): void {
    this.config = config;
  }

  private async getBaseUrl(forceRefresh: boolean = false): Promise<{ base: string; token: string }> {
    if (forceRefresh || !this.config || !this.config.token) {
      this.config = await fetchBridgeConfig(forceRefresh);
    }
    const host = this.config.host || '127.0.0.1';
    const port = this.config.port || 8766;
    return { base: `http://${host}:${port}`, token: this.config.token || '' };
  }

  private async request<T>(path: string, options: RequestInit = {}, retried: boolean = false): Promise<T> {
    const { base, token } = await this.getBaseUrl(retried);
    const url = `${base}${path.startsWith('/') ? path : '/' + path}`;
    const headers = new Headers(options.headers || {});
    if (token) headers.set('X-Bridge-Token', token);
    headers.set('Content-Type', 'application/json');

    const res = await fetch(url, { ...options, headers });
    if (res.status === 401 && !retried) {
      this.config = null;
      return this.request<T>(path, options, true);
    }
    if (!res.ok) throw new Error(`Bridge API HTTP ${res.status}: ${res.statusText}`);
    return (await res.json()) as T;
  }

  public async getLyrics(
    videoId: string,
    title?: string,
    artist?: string,
    duration?: number,
    customQuery?: string
  ): Promise<LyricsResponse> {
    const params = new URLSearchParams();
    if (title) params.set('title', title);
    if (artist) params.set('artist', artist);
    if (duration && duration > 0) params.set('duration', String(Math.round(duration)));
    if (customQuery && customQuery.trim()) params.set('q', customQuery.trim());
    const qs = params.toString();
    return this.request<LyricsResponse>(
      `/lyrics/${encodeURIComponent(videoId)}${qs ? '?' + qs : ''}`
    );
  }

  public async preloadTrack(videoId: string): Promise<void> {
    if (!videoId || this.preloadedIds.has(videoId)) return;
    this.preloadedIds.add(videoId);
    if (this.preloadedIds.size > 60) {
      const first = this.preloadedIds.values().next().value;
      if (first) this.preloadedIds.delete(first);
    }
    try {
      await this.request('/player/preload', {
        method: 'POST',
        body: JSON.stringify({ video_id: videoId }),
      });
    } catch {
      this.preloadedIds.delete(videoId);
    }
  }

  public async setEqPreset(preset: EqPreset): Promise<{ preset: EqPreset }> {
    return this.request('/player/eq', {
      method: 'POST',
      body: JSON.stringify({ preset }),
    });
  }

  public async getHealth(): Promise<{ status: string; app: string; version: string; state: string }> {
    return this.request('/health');
  }

  public async getHome(): Promise<HomeSection[]> {
    const res = await this.request<any>('/home');
    return Array.isArray(res) ? res : res.sections || [];
  }

  public async getPersonalizedHome(
    mood: string = 'for_you',
    refresh: boolean = false
  ): Promise<PersonalizedHomeResponse> {
    const params = new URLSearchParams({ mood, refresh: refresh ? 'true' : 'false' });
    return this.request<PersonalizedHomeResponse>(`/home/personalized?${params.toString()}`);
  }

  public async search(query: string, filterType: string = 'songs', limit: number = 25): Promise<Track[]> {
    const params = new URLSearchParams({ q: query, filter: filterType, limit: String(limit) });
    const res = await this.request<any>(`/search?${params.toString()}`);
    return Array.isArray(res) ? res : res.results || [];
  }

  public async getArtistProfile(name?: string, channelId?: string): Promise<ArtistProfile> {
    const params = new URLSearchParams();
    if (name && name.trim()) params.set('name', name.trim());
    if (channelId && channelId.trim()) params.set('channel_id', channelId.trim());
    return this.request<ArtistProfile>(`/artist?${params.toString()}`);
  }

  public async getAlbumDetails(opts: {
    browseId?: string;
    title?: string;
    artist?: string;
    videoId?: string;
  }): Promise<AlbumDetails> {
    const params = new URLSearchParams();
    if (opts.browseId && opts.browseId.trim()) params.set('browse_id', opts.browseId.trim());
    if (opts.title && opts.title.trim()) params.set('title', opts.title.trim());
    if (opts.artist && opts.artist.trim()) params.set('artist', opts.artist.trim());
    if (opts.videoId && opts.videoId.trim()) params.set('video_id', opts.videoId.trim());
    return this.request<AlbumDetails>(`/album?${params.toString()}`);
  }

  public async play(track?: Track, videoId?: string): Promise<{ status: string }> {
    return this.request('/player/play', {
      method: 'POST',
      body: JSON.stringify({
        video_id: videoId || track?.video_id,
        track: track,
        track_meta: track,
      }),
    });
  }

  public async pause(): Promise<PlaybackTick> {
    return this.request('/player/pause', { method: 'POST' });
  }

  public async resume(): Promise<PlaybackTick & { status?: string; error?: string }> {
    return this.request('/player/play', { method: 'POST' });
  }

  public async next(): Promise<{ status: string }> {
    return this.request('/player/next', { method: 'POST' });
  }

  public async prev(): Promise<{ status: string }> {
    return this.request('/player/prev', { method: 'POST' });
  }

  public async seek(seconds: number): Promise<{ time_pos: number }> {
    return this.request('/player/seek', {
      method: 'POST',
      body: JSON.stringify({ seconds }),
    });
  }

  public async setVolume(volume: number): Promise<{ volume: number }> {
    return this.request('/player/volume', {
      method: 'POST',
      body: JSON.stringify({ volume }),
    });
  }

  public async getQueue(): Promise<QueueData> {
    return this.request('/queue');
  }

  public async updateQueue(action: {
    action: string;
    items?: Track[];
    item?: Track;
    index?: number;
    from_idx?: number;
    to_idx?: number;
    shuffle?: boolean;
    repeat_mode?: string;
    autoplay_radio?: boolean;
    start_index?: number;
    auto_play?: boolean;
  }): Promise<QueueData> {
    return this.request('/queue', {
      method: 'POST',
      body: JSON.stringify(action),
    });
  }

  public async setQueue(items: Track[], startIndex: number = 0, autoPlay: boolean = true): Promise<QueueData> {
    return this.updateQueue({ action: 'set', items, start_index: startIndex, auto_play: autoPlay });
  }

  public async jumpQueue(index: number, autoPlay: boolean = true): Promise<QueueData> {
    return this.updateQueue({ action: 'jump', index, auto_play: autoPlay });
  }

  public async appendQueue(itemOrItems: Track | Track[]): Promise<QueueData> {
    return Array.isArray(itemOrItems)
      ? this.updateQueue({ action: 'append', items: itemOrItems })
      : this.updateQueue({ action: 'append', item: itemOrItems });
  }

  public async addNextQueue(itemOrItems: Track | Track[]): Promise<QueueData> {
    return Array.isArray(itemOrItems)
      ? this.updateQueue({ action: 'add_next', items: itemOrItems })
      : this.updateQueue({ action: 'add_next', item: itemOrItems });
  }

  public async removeQueue(index: number): Promise<QueueData> {
    return this.updateQueue({ action: 'remove', index });
  }

  public async moveQueue(from_idx: number, to_idx: number): Promise<QueueData> {
    return this.updateQueue({ action: 'move', from_idx, to_idx });
  }

  public async clearQueue(): Promise<QueueData> {
    return this.updateQueue({ action: 'clear' });
  }

  public async setShuffle(shuffle?: boolean): Promise<QueueData> {
    return this.updateQueue({ action: 'shuffle', shuffle });
  }

  public async setRepeat(repeat_mode?: 'off' | 'all' | 'one'): Promise<QueueData> {
    return this.updateQueue({ action: 'repeat', repeat_mode });
  }

  public async setAutoplayRadio(autoplay_radio?: boolean): Promise<QueueData> {
    return this.updateQueue({ action: 'autoplay_radio', autoplay_radio });
  }

  public async toggleLike(videoId: string, track?: Track): Promise<{ video_id: string; is_liked: boolean }> {
    return this.request(`/liked/${encodeURIComponent(videoId)}`, {
      method: 'POST',
      body: JSON.stringify({ track_meta: track }),
    });
  }

  public async getLiked(searchQuery?: string): Promise<Track[]> {
    const q = searchQuery ? `?q=${encodeURIComponent(searchQuery)}` : '';
    const res = await this.request<any>(`/liked${q}`);
    return Array.isArray(res) ? res : res.tracks || [];
  }

  public async getHistory(
    limit: number = 100,
    sort: 'recent' | 'most_played' = 'recent'
  ): Promise<Track[]> {
    const params = new URLSearchParams({ limit: String(limit), sort });
    const res = await this.request<any>(`/history?${params.toString()}`);
    return Array.isArray(res) ? res : res.tracks || [];
  }

  public async getPlaylists(): Promise<Playlist[]> {
    const res = await this.request<any>('/playlists');
    return Array.isArray(res) ? res : res.playlists || [];
  }

  public async createPlaylist(name: string, description: string = ''): Promise<Playlist> {
    return this.request('/playlists', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    });
  }

  public async deletePlaylist(id: string): Promise<{ status: string }> {
    return this.request(`/playlists/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  public async getPlaylistTracks(id: string): Promise<Track[]> {
    const res = await this.request<any>(`/playlists/${encodeURIComponent(id)}/tracks`);
    return Array.isArray(res) ? res : res.tracks || [];
  }

  public async addPlaylistTracks(id: string, tracks: Track[]): Promise<{ status: string }> {
    return this.request(`/playlists/${encodeURIComponent(id)}/tracks`, {
      method: 'POST',
      body: JSON.stringify({ tracks }),
    });
  }

  public async removePlaylistTrack(id: string, videoId: string): Promise<{ status: string }> {
    return this.request(`/playlists/${encodeURIComponent(id)}/tracks`, {
      method: 'DELETE',
      body: JSON.stringify({ video_id: videoId }),
    });
  }

  public async startDownload(videoId: string, track?: Track): Promise<{ status: string }> {
    return this.request(`/downloads/${encodeURIComponent(videoId)}`, {
      method: 'POST',
      body: JSON.stringify({ track_meta: track }),
    });
  }

  public async deleteDownload(videoId: string): Promise<{ status: string }> {
    return this.request(`/downloads/${encodeURIComponent(videoId)}`, { method: 'DELETE' });
  }

  public async getDownloads(): Promise<Track[]> {
    const res = await this.request<any>('/downloads');
    return Array.isArray(res) ? res : res.downloads || [];
  }

  public async getSettings(): Promise<Record<string, unknown>> {
    return this.request('/settings');
  }

  public async setSettings(settings: Record<string, unknown>): Promise<{ status: string }> {
    return this.request('/settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    });
  }

  public async getUpdateStatus(): Promise<UpdateState> {
    return this.request('/updates/status');
  }

  public async checkForUpdates(force: boolean = true): Promise<UpdateState> {
    return this.request(`/updates/check?force=${force ? 'true' : 'false'}`, {
      method: 'POST',
    });
  }

  public async downloadUpdate(autoInstall: boolean = true): Promise<UpdateState> {
    return this.request(`/updates/download?auto_install=${autoInstall ? 'true' : 'false'}`, {
      method: 'POST',
    });
  }

  public async installUpdate(): Promise<UpdateState> {
    return this.request('/updates/install', {
      method: 'POST',
    });
  }
}

export const bridgeApi = new BridgeApiClient();

export type EventListener<K extends keyof BridgeEventMap> = (data: BridgeEventMap[K]) => void;

export class BridgeSocketClient {
  private ws: WebSocket | null = null;
  private config: BridgeConfig | null = null;
  private listeners: { [K in keyof BridgeEventMap]?: Set<EventListener<K>> } = {};
  private reconnectTimer: number | null = null;
  private retryAttempt = 0;
  private isDestroyed = false;
  private statusListeners: Set<(connected: boolean) => void> = new Set();

  public isConnected = false;
  public latestTimePos = 0;
  public latestDuration = 0;
  public latestTrackId: string | null = null;

  public onStatusChange(callback: (connected: boolean) => void): () => void {
    this.statusListeners.add(callback);
    callback(this.isConnected);
    return () => this.statusListeners.delete(callback);
  }

  private notifyStatus(connected: boolean) {
    if (this.isConnected !== connected) {
      this.isConnected = connected;
      this.statusListeners.forEach((cb) => cb(connected));
    }
  }

  public async connect(): Promise<void> {
    if (this.isDestroyed) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      this.config = await fetchBridgeConfig(this.retryAttempt > 0);
      const host = this.config.host || '127.0.0.1';
      const port = this.config.port || 8766;
      const token = this.config.token || '';
      if (token) {
        bridgeApi.setConfig(this.config);
      }

      const query = token ? `?token=${encodeURIComponent(token)}` : '';
      const wsUrl = `ws://${host}:${port}/ws${query}`;

      const socket = new WebSocket(wsUrl);
      this.ws = socket;

      socket.onopen = () => {
        this.retryAttempt = 0;
        this.notifyStatus(true);
      };

      socket.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data) as { event: keyof BridgeEventMap; data: any };
          if (parsed && parsed.event) {
            if (parsed.event === 'playback_tick' && parsed.data) {
              if (typeof parsed.data.time_pos === 'number') this.latestTimePos = parsed.data.time_pos;
              if (typeof parsed.data.duration === 'number') this.latestDuration = parsed.data.duration;
              this.latestTrackId = parsed.data.current_track?.video_id ?? null;
            } else if (parsed.event === 'track_changed' && parsed.data) {
              this.latestTimePos = typeof parsed.data.time_pos === 'number' ? parsed.data.time_pos : 0;
              const trk =
                parsed.data.current_track !== undefined ? parsed.data.current_track : parsed.data.track ?? null;
              this.latestDuration =
                typeof parsed.data.duration === 'number' ? parsed.data.duration : trk?.duration_s ?? 0;
              this.latestTrackId = trk?.video_id ?? null;
            }
            if (this.listeners[parsed.event]) {
              const handlers = this.listeners[parsed.event];
              if (handlers) handlers.forEach((handler) => (handler as any)(parsed.data));
            }
          }
        } catch {
          // Ignore invalid frames
        }
      };

      socket.onclose = () => {
        this.notifyStatus(false);
        this.ws = null;
        this.scheduleReconnect();
      };

      socket.onerror = () => {
        this.notifyStatus(false);
        if (socket.readyState === WebSocket.OPEN) socket.close();
      };
    } catch {
      this.notifyStatus(false);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect() {
    if (this.isDestroyed || this.reconnectTimer !== null) return;
    this.retryAttempt++;
    const delay = Math.min(1000 * Math.pow(1.5, this.retryAttempt - 1), 4000);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  public on<K extends keyof BridgeEventMap>(event: K, listener: EventListener<K>): () => void {
    if (!this.listeners[event]) this.listeners[event] = new Set() as any;
    (this.listeners[event] as Set<EventListener<K>>).add(listener);
    return () => {
      this.listeners[event]?.delete(listener);
    };
  }

  public disconnect(): void {
    this.isDestroyed = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.notifyStatus(false);
  }
}

export const bridgeSocket = new BridgeSocketClient();

export function useBridge() {
  const [config, setConfig] = useState<BridgeConfig | null>(cachedConfig);
  const [isConnected, setIsConnected] = useState<boolean>(bridgeSocket.isConnected);
  const [playback, setPlayback] = useState<PlaybackTick | null>(null);
  const [queue, setQueue] = useState<QueueData | null>(null);
  const [downloads, setDownloads] = useState<Record<string, DownloadProgress>>({});
  const [updateState, setUpdateState] = useState<UpdateState | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const mountedRef = useRef(true);

  const syncInitialState = useCallback(async () => {
    try {
      const cfg = await fetchBridgeConfig();
      if (!mountedRef.current) return;
      setConfig(cfg);
      bridgeApi.setConfig(cfg);

      const [qData, healthData, updData] = await Promise.allSettled([
        bridgeApi.getQueue(),
        bridgeApi.getHealth(),
        bridgeApi.getUpdateStatus(),
      ]);

      if (qData.status === 'fulfilled' && mountedRef.current) setQueue(qData.value);
      if (healthData.status === 'fulfilled' && mountedRef.current) setLastError(null);
      if (updData.status === 'fulfilled' && mountedRef.current) setUpdateState(updData.value);
    } catch (err: any) {
      if (mountedRef.current) setLastError(err?.message || 'Failed connecting to ORBIT bridge');
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    syncInitialState();
    bridgeSocket.connect();

    const unsubStatus = bridgeSocket.onStatusChange((connected) => {
      if (mountedRef.current) {
        setIsConnected(connected);
        if (connected) syncInitialState();
      }
    });

    const unsubPlayback = bridgeSocket.on('playback_tick', (tick) => {
      if (mountedRef.current) {
        if (tick.state === 'playing') setLastError(null);
        setPlayback((prev) => {
          if (
            prev &&
            prev.state === tick.state &&
            prev.volume === tick.volume &&
            Math.round(prev.duration) === Math.round(tick.duration) &&
            (prev.current_track?.video_id ?? null) === (tick.current_track?.video_id ?? null) &&
            (prev.current_track?.title ?? null) === (tick.current_track?.title ?? null)
          ) {
            return prev;
          }
          return tick;
        });
      }
    });

    const unsubTrackChanged = bridgeSocket.on('track_changed', (evt) => {
      if (mountedRef.current) {
        const nextTrack = evt.current_track !== undefined ? evt.current_track : evt.track ?? null;
        setPlayback((prev) => ({
          time_pos: evt.time_pos ?? 0,
          duration: evt.duration ?? nextTrack?.duration_s ?? 0,
          state: evt.state ?? (nextTrack ? 'playing' : 'stopped'),
          volume: prev ? prev.volume : 80,
          current_track: nextTrack,
        }));
      }
    });

    const unsubQueue = bridgeSocket.on('queue_changed', (qData) => {
      if (mountedRef.current) setQueue(qData);
    });

    const unsubDownload = bridgeSocket.on('download_progress', (p) => {
      if (mountedRef.current) {
        setDownloads((prev) => ({ ...prev, [p.video_id]: p }));
      }
    });

    const unsubUpdate = bridgeSocket.on('update_status', (uState) => {
      if (mountedRef.current && uState) {
        setUpdateState(uState);
      }
    });

    const unsubError = bridgeSocket.on('error', (errEvt) => {
      if (mountedRef.current && errEvt?.message) {
        setLastError(errEvt.message);
      }
    });

    return () => {
      mountedRef.current = false;
      unsubStatus();
      unsubPlayback();
      unsubTrackChanged();
      unsubQueue();
      unsubDownload();
      unsubUpdate();
      unsubError();
    };
  }, [syncInitialState]);

  const reconnect = useCallback(() => {
    bridgeSocket.connect();
    syncInitialState();
  }, [syncInitialState]);

  return {
    config,
    isConnected,
    playback,
    setPlayback,
    queue,
    setQueue,
    downloads,
    setDownloads,
    updateState,
    setUpdateState,
    api: bridgeApi,
    lastError,
    reconnect,
  };
}
