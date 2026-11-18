/**
 * Typed REST & WebSocket client with auto-reconnect for MetroLite Bridge.
 * Adheres strictly to security rules: never logs or echoes auth tokens.
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import type {
  BridgeConfig,
  BridgeEventMap,
  DownloadProgress,
  HomeSection,
  PlaybackTick,
  Playlist,
  QueueData,
  Track,
} from './types';

export const isTauri = (): boolean => {
  return typeof window !== 'undefined' && ('__TAURI_INTERNALS__' in window || '__TAURI__' in window);
};

let cachedConfig: BridgeConfig | null = null;

export async function fetchBridgeConfig(): Promise<BridgeConfig> {
  if (cachedConfig) {
    return cachedConfig;
  }

  if (isTauri()) {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const cfg = await invoke<BridgeConfig>('get_bridge_config');
      cachedConfig = cfg;
      return cfg;
    } catch {
      // Fallback if invoke fails
    }
  }

  // Fallback for Vite browser dev server / non-tauri mode
  try {
    const res = await fetch('/__bridge_config');
    if (res.ok) {
      const cfg = (await res.json()) as BridgeConfig;
      if (typeof window !== 'undefined' && window.location.hostname === 'localhost') {
        cfg.host = 'localhost';
      }
      cachedConfig = cfg;
      return cfg;
    }
  } catch {
    // Ignore dev fallback error
  }

  const fallbackHost = typeof window !== 'undefined' && window.location.hostname ? window.location.hostname : '127.0.0.1';
  const fallback: BridgeConfig = {
    host: fallbackHost,
    port: 8766,
    token: '',
  };
  cachedConfig = fallback;
  return fallback;
}

export function setCachedBridgeConfig(config: BridgeConfig): void {
  cachedConfig = config;
}

/**
 * Window caption action handlers (Tauri or browser fallback)
 */
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

/**
 * REST API client targeting local FastAPI bridge.
 */
export class BridgeApiClient {
  private config: BridgeConfig | null = null;

  constructor(config?: BridgeConfig) {
    if (config) {
      this.config = config;
    }
  }

  public setConfig(config: BridgeConfig): void {
    this.config = config;
  }

  private async getBaseUrl(): Promise<{ base: string; token: string }> {
    if (!this.config) {
      this.config = await fetchBridgeConfig();
    }
    const host = this.config.host || '127.0.0.1';
    const port = this.config.port || 8766;
    return {
      base: `http://${host}:${port}`,
      token: this.config.token || '',
    };
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const { base, token } = await this.getBaseUrl();
    const url = `${base}${path.startsWith('/') ? path : '/' + path}`;
    const headers = new Headers(options.headers || {});
    if (token) {
      headers.set('X-Bridge-Token', token);
    }
    headers.set('Content-Type', 'application/json');

    const res = await fetch(url, {
      ...options,
      headers,
    });

    if (!res.ok) {
      throw new Error(`Bridge API HTTP ${res.status}: ${res.statusText}`);
    }

    return (await res.json()) as T;
  }

  public async getHealth(): Promise<{ status: string; app: string; version: string; state: string }> {
    return this.request('/health');
  }

  public async getHome(): Promise<HomeSection[]> {
    const res = await this.request<any>('/home');
    if (Array.isArray(res)) return res;
    return res.sections || [];
  }

  public async search(query: string, filterType: string = 'songs', limit: number = 25): Promise<Track[]> {
    const params = new URLSearchParams({ q: query, filter: filterType, limit: String(limit) });
    const res = await this.request<any>(`/search?${params.toString()}`);
    if (Array.isArray(res)) return res;
    return res.results || [];
  }

  public async play(track?: Track, videoId?: string): Promise<{ status: string }> {
    return this.request('/player/play', {
      method: 'POST',
      body: JSON.stringify({
        video_id: videoId || track?.video_id,
        track_meta: track,
      }),
    });
  }

  public async pause(): Promise<PlaybackTick> {
    return this.request('/player/pause', { method: 'POST' });
  }

  public async resume(): Promise<{ status: string }> {
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
    return this.updateQueue({
      action: 'set',
      items,
      start_index: startIndex,
      auto_play: autoPlay,
    });
  }

  public async appendQueue(itemOrItems: Track | Track[]): Promise<QueueData> {
    if (Array.isArray(itemOrItems)) {
      return this.updateQueue({ action: 'append', items: itemOrItems });
    }
    return this.updateQueue({ action: 'append', item: itemOrItems });
  }

  public async addNextQueue(itemOrItems: Track | Track[]): Promise<QueueData> {
    if (Array.isArray(itemOrItems)) {
      return this.updateQueue({ action: 'add_next', items: itemOrItems });
    }
    return this.updateQueue({ action: 'add_next', item: itemOrItems });
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
    if (Array.isArray(res)) return res;
    return res.tracks || [];
  }

  public async getPlaylists(): Promise<Playlist[]> {
    const res = await this.request<any>('/playlists');
    if (Array.isArray(res)) return res;
    return res.playlists || [];
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
    if (Array.isArray(res)) return res;
    return res.tracks || [];
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
    return this.request(`/downloads/${encodeURIComponent(videoId)}`, {
      method: 'DELETE',
    });
  }

  public async getDownloads(): Promise<Track[]> {
    const res = await this.request<any>('/downloads');
    if (Array.isArray(res)) return res;
    return res.downloads || [];
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
}

export const bridgeApi = new BridgeApiClient();

/**
 * WebSocket event listener manager with automatic reconnection.
 */
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
      this.config = await fetchBridgeConfig();
      const host = this.config.host || '127.0.0.1';
      const port = this.config.port || 8766;
      const token = this.config.token || '';

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
          if (parsed && parsed.event && this.listeners[parsed.event]) {
            const handlers = this.listeners[parsed.event];
            if (handlers) {
              handlers.forEach((handler) => (handler as any)(parsed.data));
            }
          }
        } catch {
          // Ignore invalid frames silently
        }
      };

      socket.onclose = () => {
        this.notifyStatus(false);
        this.ws = null;
        this.scheduleReconnect();
      };

      socket.onerror = () => {
        this.notifyStatus(false);
        if (socket.readyState === WebSocket.OPEN) {
          socket.close();
        }
      };
    } catch {
      this.notifyStatus(false);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect() {
    if (this.isDestroyed || this.reconnectTimer !== null) return;
    this.retryAttempt++;
    // Exponential backoff capped at 4000ms
    const delay = Math.min(1000 * Math.pow(1.5, this.retryAttempt - 1), 4000);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  public on<K extends keyof BridgeEventMap>(event: K, listener: EventListener<K>): () => void {
    if (!this.listeners[event]) {
      this.listeners[event] = new Set() as any;
    }
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

/**
 * React hook to access real-time MetroLite bridge connection and playback state.
 */
export function useBridge() {
  const [config, setConfig] = useState<BridgeConfig | null>(cachedConfig);
  const [isConnected, setIsConnected] = useState<boolean>(bridgeSocket.isConnected);
  const [playback, setPlayback] = useState<PlaybackTick | null>(null);
  const [queue, setQueue] = useState<QueueData | null>(null);
  const [downloads, setDownloads] = useState<Record<string, DownloadProgress>>({});
  const [lastError, setLastError] = useState<string | null>(null);

  const mountedRef = useRef(true);

  const syncInitialState = useCallback(async () => {
    try {
      const cfg = await fetchBridgeConfig();
      if (!mountedRef.current) return;
      setConfig(cfg);
      bridgeApi.setConfig(cfg);

      const [qData, healthData] = await Promise.allSettled([
        bridgeApi.getQueue(),
        bridgeApi.getHealth(),
      ]);

      if (qData.status === 'fulfilled' && mountedRef.current) {
        setQueue(qData.value);
      }
      if (healthData.status === 'fulfilled' && mountedRef.current) {
        setLastError(null);
      }
    } catch (err: any) {
      if (mountedRef.current) {
        setLastError(err?.message || 'Failed connecting to MetroLite bridge');
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    syncInitialState();
    bridgeSocket.connect();

    const unsubStatus = bridgeSocket.onStatusChange((connected) => {
      if (mountedRef.current) {
        setIsConnected(connected);
        if (connected) {
          syncInitialState();
        }
      }
    });

    const unsubPlayback = bridgeSocket.on('playback_tick', (tick) => {
      if (mountedRef.current) {
        setPlayback(tick);
      }
    });

    const unsubTrackChanged = bridgeSocket.on('track_changed', (evt) => {
      if (mountedRef.current) {
        setPlayback((prev) => ({
          time_pos: evt.time_pos,
          duration: evt.duration,
          state: evt.state,
          volume: prev ? prev.volume : 80,
          current_track: evt.current_track,
        }));
      }
    });

    const unsubQueue = bridgeSocket.on('queue_changed', (qData) => {
      if (mountedRef.current) {
        setQueue(qData);
      }
    });

    const unsubDownload = bridgeSocket.on('download_progress', (p) => {
      if (mountedRef.current) {
        setDownloads((prev) => ({
          ...prev,
          [p.video_id]: p,
        }));
      }
    });

    return () => {
      mountedRef.current = false;
      unsubStatus();
      unsubPlayback();
      unsubTrackChanged();
      unsubQueue();
      unsubDownload();
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
    api: bridgeApi,
    lastError,
    reconnect,
  };
}
