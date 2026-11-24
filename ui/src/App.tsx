import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { AlertCircle, X } from 'lucide-react';
import { StarfieldBg, type CosmicEffectsMode } from './components/StarfieldBg';
import {
  BlackHoleTransition,
  type SuckAnimationPayload,
} from './components/BlackHoleTransition';
import { DynamicIsland } from './components/DynamicIsland';
import { Titlebar } from './components/Titlebar';
import { Sidebar, type NavTab } from './components/Sidebar';
import { PlayerBar } from './components/PlayerBar';
import { QueueDrawer } from './components/QueueDrawer';
import { LyricsDrawer } from './components/LyricsDrawer';
import { FullscreenStage, type FullscreenTab } from './components/FullscreenStage';
import { ToastHost, toast } from './components/ToastHost';
import { HomeView } from './views/HomeView';
import { SearchView } from './views/SearchView';
import { LibraryView } from './views/LibraryView';
import { ArtistView, type ArtistNavTarget, type AlbumNavTarget } from './views/ArtistView';
import { AlbumView } from './views/AlbumView';
import {
  CreatePlaylistModal,
  AddToPlaylistModal,
  SettingsModal,
} from './components/Modals';
import { UpdateAvailableModal } from './components/UpdateCenter';
import {
  useBridge,
  bridgeSocket,
  isTauri,
  enterDynamicIslandWindow,
  exitDynamicIslandWindow,
  launchUpdateInstallerAndQuit,
  trimProcessMemory,
  waitForNextPaint,
  type SavedWindowBounds,
} from './api/client';
import { extractTrackAccent, DEFAULT_COSMIC_ACCENT } from './utils/colorExtract';
import type { Track, Playlist, EqPreset } from './api/types';

type RightDrawerTab = 'none' | 'queue' | 'lyrics';

export default function App() {
  const {
    isConnected,
    playback,
    setPlayback,
    queue,
    setQueue,
    downloads,
    setDownloads,
    updateState,
    setUpdateState,
    api,
    lastError,
  } = useBridge();

  const [isUpdateTabOpen, setIsUpdateTabOpen] = useState<boolean>(false);
  const [isUpdateModalOpen, setIsUpdateModalOpen] = useState<boolean>(false);
  const promptedUpdateVersionRef = useRef<string | null>(null);
  const toastedReadyUpdateRef = useRef<string | null>(null);
  const autoInstallPendingRef = useRef<boolean>(false);

  useEffect(() => {
    if (
      updateState?.update_available &&
      updateState.latest_version &&
      (updateState.status === 'available' || updateState.status === 'ready') &&
      promptedUpdateVersionRef.current !== updateState.latest_version
    ) {
      promptedUpdateVersionRef.current = updateState.latest_version;
      setIsUpdateModalOpen(true);
    }
    if (
      updateState?.status === 'ready' &&
      updateState.latest_version &&
      toastedReadyUpdateRef.current !== updateState.latest_version
    ) {
      toastedReadyUpdateRef.current = updateState.latest_version;
      toast.show(`Update v${updateState.latest_version} downloaded`, 'success', 3600);
    }
    if (
      autoInstallPendingRef.current &&
      updateState?.status === 'ready' &&
      updateState.downloaded_path
    ) {
      autoInstallPendingRef.current = false;
      void launchUpdateInstallerAndQuit(updateState.downloaded_path).then((launched) => {
        if (!launched) {
          void api.installUpdate().then((next) => setUpdateState(next)).catch(() => {});
        }
      });
    } else if (updateState?.status === 'error') {
      autoInstallPendingRef.current = false;
    }
  }, [
    api,
    setUpdateState,
    updateState?.downloaded_path,
    updateState?.latest_version,
    updateState?.status,
    updateState?.update_available,
  ]);

  const [dismissedError, setDismissedError] = useState<string | null>(null);

  useEffect(() => {
    if (!lastError) {
      setDismissedError(null);
      return;
    }
    setDismissedError(null);
    const timer = window.setTimeout(() => {
      setDismissedError(lastError);
    }, 6500);
    return () => clearTimeout(timer);
  }, [lastError]);

  const visibleError = lastError && lastError !== dismissedError ? lastError : null;

  const [activeTab, setActiveTab] = useState<NavTab>('home');
  const [activePlaylistId, setActivePlaylistId] = useState<string | null>(null);
  const [activeArtist, setActiveArtist] = useState<ArtistNavTarget | null>(null);
  const [activeAlbum, setActiveAlbum] = useState<AlbumNavTarget | null>(null);
  const [historyStack, setHistoryStack] = useState<
    {
      tab: NavTab;
      playlistId?: string | null;
      artist?: ArtistNavTarget | null;
      album?: AlbumNavTarget | null;
    }[]
  >([{ tab: 'home' }]);
  const [historyIndex, setHistoryIndex] = useState<number>(0);

  const [searchQuery, setSearchQuery] = useState<string>('');
  const [submittedSearch, setSubmittedSearch] = useState<string>('');

  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(true);
  // Queue and Docked Lyrics share the right-hand slot (mutually exclusive)
  const [rightDrawer, setRightDrawer] = useState<RightDrawerTab>('none');
  const [isFullscreenStageOpen, setIsFullscreenStageOpen] = useState<boolean>(false);
  const [stageInitialTab, setStageInitialTab] = useState<FullscreenTab>('lyrics');
  const [isSettingsOpen, setIsSettingsOpen] = useState<boolean>(false);
  const [isCreatePlaylistOpen, setIsCreatePlaylistOpen] = useState<boolean>(false);
  const [addToPlaylistTrack, setAddToPlaylistTrack] = useState<Track | null>(null);

  const [isIslandMode, setIsIslandMode] = useState<boolean>(false);
  const [isWindowTransitioning, setIsWindowTransitioning] = useState<boolean>(false);
  const [restoreSeq, setRestoreSeq] = useState<number>(0);
  const savedBoundsRef = useRef<SavedWindowBounds | null>(null);
  const islandSwitchingRef = useRef<boolean>(false);

  const [suckPayload, setSuckPayload] = useState<SuckAnimationPayload | null>(null);
  const [absorbPulseKey, setAbsorbPulseKey] = useState<number>(0);
  const [playerRiseKey, setPlayerRiseKey] = useState<number>(0);
  const lastTrackRef = useRef<Track | null>(null);
  const isIslandModeRef = useRef<boolean>(isIslandMode);
  const isFullscreenStageOpenRef = useRef<boolean>(isFullscreenStageOpen);
  isIslandModeRef.current = isIslandMode;
  isFullscreenStageOpenRef.current = isFullscreenStageOpen;

  const [cosmicMode, setCosmicMode] = useState<CosmicEffectsMode>('full');
  const [autoDownloadLiked, setAutoDownloadLiked] = useState<boolean>(false);
  const [eqPreset, setEqPreset] = useState<EqPreset>('flat');
  const appliedEqRef = useRef<EqPreset | null>(null);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);
  const refreshPendingRef = useRef<boolean>(false);

  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [likedTracks, setLikedTracks] = useState<Track[]>([]);
  const [downloadTracks, setDownloadTracks] = useState<Track[]>([]);
  const [likedIds, setLikedIds] = useState<Set<string>>(new Set());
  const [libraryVersion, setLibraryVersion] = useState<number>(0);

  const downloadedIds = useMemo(
    () => new Set(downloadTracks.map((t) => t.video_id)),
    [downloadTracks]
  );

  useEffect(() => {
    let trimTimer: number | null = null;
    const syncPause = () => {
      const paused = document.hidden || !document.hasFocus();
      document.documentElement.classList.toggle('window-paused', paused);
      if (trimTimer !== null) {
        clearTimeout(trimTimer);
        trimTimer = null;
      }
      if (paused) {
        trimTimer = window.setTimeout(() => {
          void trimProcessMemory();
        }, 1500);
      }
    };
    const idleInterval = window.setInterval(() => {
      if (document.hidden || !document.hasFocus()) {
        void trimProcessMemory();
      }
    }, 45000);
    window.addEventListener('blur', syncPause);
    window.addEventListener('focus', syncPause);
    document.addEventListener('visibilitychange', syncPause);
    syncPause();
    return () => {
      if (trimTimer !== null) clearTimeout(trimTimer);
      clearInterval(idleInterval);
      window.removeEventListener('blur', syncPause);
      window.removeEventListener('focus', syncPause);
      document.removeEventListener('visibilitychange', syncPause);
    };
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-effects', cosmicMode);
  }, [cosmicMode]);

  const cosmicModeRef = useRef<CosmicEffectsMode>(cosmicMode);
  cosmicModeRef.current = cosmicMode;

  const activeTrack = playback?.current_track || queue?.current_track || null;
  const activeVideoId = activeTrack?.video_id || null;
  const activeThumb = activeTrack?.thumbnail_url || null;
  const activeTrackRef = useRef<Track | null>(activeTrack);
  activeTrackRef.current = activeTrack;

  useEffect(() => {
    const curr = activeTrackRef.current;
    if (!activeVideoId || !curr) return;
    const prev = lastTrackRef.current;
    lastTrackRef.current = curr;

    if (prev && prev.video_id !== activeVideoId) {
      setPlayerRiseKey((k) => k + 1);

      // Suppress BlackHole suck-in animation when Fullscreen Stage or Dynamic Island obscures the canvas
      if (
        !isIslandModeRef.current &&
        !isFullscreenStageOpenRef.current &&
        cosmicModeRef.current === 'full'
      ) {
        const nowId = Date.now();
        setSuckPayload({ id: nowId, track: prev });
        const pulseTimer = window.setTimeout(() => {
          setAbsorbPulseKey((k) => k + 1);
        }, 640);
        return () => {
          clearTimeout(pulseTimer);
        };
      }

      setSuckPayload(null);
    }
  }, [activeVideoId]);

  useEffect(() => {
    if (isIslandMode || isFullscreenStageOpen) {
      setSuckPayload(null);
      if (activeTrackRef.current) {
        lastTrackRef.current = activeTrackRef.current;
      }
    }
  }, [isIslandMode, isFullscreenStageOpen]);

  useEffect(() => {
    document.title = activeTrack?.title
      ? `${activeTrack.title} — ${activeTrack.artist} • ORBIT`
      : 'ORBIT';
  }, [activeTrack?.title, activeTrack?.artist]);

  useEffect(() => {
    let mounted = true;
    const applyAccent = (accent = DEFAULT_COSMIC_ACCENT) => {
      const root = document.documentElement;
      root.style.setProperty('--dynamic-accent', accent.hex);
      root.style.setProperty('--dynamic-accent-rgb', accent.rgb);
      root.style.setProperty('--dynamic-secondary', accent.secondaryHex);
      root.style.setProperty('--dynamic-secondary-rgb', accent.secondaryRgb);
    };

    if (!activeVideoId) {
      applyAccent({
        hex: '#2DD4BF',
        rgb: '45, 212, 191',
        secondaryHex: '#6366F1',
        secondaryRgb: '99, 102, 241',
      });
      return;
    }

    extractTrackAccent(activeVideoId, activeThumb).then((accent) => {
      if (mounted) applyAccent(accent);
    });

    return () => {
      mounted = false;
    };
  }, [activeVideoId, activeThumb]);

  const navigateTo = useCallback(
    (
      tab: NavTab,
      playlistId?: string | null,
      artist?: ArtistNavTarget | null,
      album?: AlbumNavTarget | null
    ) => {
      setActiveTab(tab);
      setActivePlaylistId(playlistId || null);
      if (tab === 'artist') setActiveArtist(artist || null);
      if (tab === 'album') setActiveAlbum(album || null);
      setHistoryStack((prev) => {
        const nextStack = prev.slice(0, historyIndex + 1);
        return [
          ...nextStack,
          {
            tab,
            playlistId: playlistId || null,
            artist: tab === 'artist' ? artist || null : null,
            album: tab === 'album' ? album || null : null,
          },
        ];
      });
      setHistoryIndex((prev) => prev + 1);
    },
    [historyIndex]
  );

  const handleGoBack = useCallback(() => {
    if (historyIndex > 0) {
      const nextIdx = historyIndex - 1;
      const target = historyStack[nextIdx];
      setHistoryIndex(nextIdx);
      setActiveTab(target.tab);
      setActivePlaylistId(target.playlistId || null);
      if (target.artist) setActiveArtist(target.artist);
      if (target.album) setActiveAlbum(target.album);
    }
  }, [historyIndex, historyStack]);

  const handleGoForward = useCallback(() => {
    if (historyIndex < historyStack.length - 1) {
      const nextIdx = historyIndex + 1;
      const target = historyStack[nextIdx];
      setHistoryIndex(nextIdx);
      setActiveTab(target.tab);
      setActivePlaylistId(target.playlistId || null);
      if (target.artist) setActiveArtist(target.artist);
      if (target.album) setActiveAlbum(target.album);
    }
  }, [historyIndex, historyStack]);

  const handleOpenArtist = useCallback(
    (target: ArtistNavTarget) => {
      const cleaned = target.name.split(',')[0].trim();
      if (!cleaned) return;
      setIsFullscreenStageOpen(false);
      navigateTo('artist', null, { name: cleaned, channelId: target.channelId || null }, null);
    },
    [navigateTo]
  );

  const handleOpenAlbum = useCallback(
    (target: AlbumNavTarget) => {
      setIsFullscreenStageOpen(false);
      navigateTo('album', null, null, target);
    },
    [navigateTo]
  );

  const handleOpenTrackArtist = useCallback(
    (track: Track) => {
      handleOpenArtist({
        name: track.artist.split(',')[0].trim(),
        channelId: track.artist_id || null,
      });
    },
    [handleOpenArtist]
  );

  const handleOpenTrackAlbum = useCallback(
    (track: Track) => {
      handleOpenAlbum({
        browseId: track.album_id || null,
        title: track.album || track.title,
        artist: track.artist,
        videoId: track.video_id,
      });
    },
    [handleOpenAlbum]
  );

  const handleCloseSearch = useCallback(() => {
    setSearchQuery('');
    setSubmittedSearch('');
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    if (activeTab === 'search') {
      navigateTo('home');
    }
  }, [activeTab, navigateTo]);

  const applySettingsPayload = useCallback(
    (settingsObj: Record<string, unknown> | null | undefined) => {
      if (!settingsObj) return;
      if (typeof settingsObj.auto_download_liked === 'boolean') {
        setAutoDownloadLiked(settingsObj.auto_download_liked);
      }
      const savedMode = settingsObj.cosmic_mode;
      if (savedMode === 'full' || savedMode === 'static' || savedMode === 'oled') {
        setCosmicMode(savedMode);
      } else if (settingsObj.oled_mode === true) {
        setCosmicMode('oled');
      }
      const savedEq = settingsObj.eq_preset;
      if (
        savedEq === 'flat' ||
        savedEq === 'bass' ||
        savedEq === 'vocal' ||
        savedEq === 'night' ||
        savedEq === 'studio'
      ) {
        setEqPreset(savedEq);
        if (appliedEqRef.current !== savedEq) {
          appliedEqRef.current = savedEq;
          void api.setEqPreset(savedEq).catch(() => {});
        }
      }
    },
    [api]
  );

  const refreshLibraryStats = useCallback(async () => {
    if (refreshInFlightRef.current) {
      refreshPendingRef.current = true;
      return refreshInFlightRef.current;
    }

    const run = (async () => {
      try {
        const [pls, lk, dl, settings] = await Promise.allSettled([
          api.getPlaylists(),
          api.getLiked(),
          api.getDownloads(),
          api.getSettings(),
        ]);
        if (pls.status === 'fulfilled') setPlaylists(pls.value);
        if (lk.status === 'fulfilled') {
          setLikedTracks(lk.value);
          setLikedIds(new Set(lk.value.map((t) => t.video_id)));
        }
        if (dl.status === 'fulfilled') setDownloadTracks(dl.value);
        if (settings.status === 'fulfilled') {
          applySettingsPayload(settings.value);
        }
        setLibraryVersion((v) => v + 1);
      } catch {
        // Ignore
      } finally {
        refreshInFlightRef.current = null;
        if (refreshPendingRef.current) {
          refreshPendingRef.current = false;
          void refreshLibraryStats();
        }
      }
    })();

    refreshInFlightRef.current = run;
    return run;
  }, [api, applySettingsPayload]);

  const refreshLibraryByEvent = useCallback(
    async (evtType?: string) => {
      try {
        if (evtType === 'liked') {
          const lk = await api.getLiked();
          setLikedTracks(lk);
          setLikedIds(new Set(lk.map((t) => t.video_id)));
          return;
        }
        if (evtType === 'download_updated' || evtType === 'download_deleted') {
          const dl = await api.getDownloads();
          setDownloadTracks(dl);
          return;
        }
        if (
          evtType === 'playlist_created' ||
          evtType === 'playlist_deleted' ||
          evtType === 'playlist_tracks_added' ||
          evtType === 'playlist_tracks_removed'
        ) {
          const pls = await api.getPlaylists();
          setPlaylists(pls);
          setLibraryVersion((v) => v + 1);
          return;
        }
        if (evtType === 'settings_updated') {
          const settings = await api.getSettings();
          applySettingsPayload(settings);
          return;
        }
      } catch {
        // Fall through on transient error
      }
      await refreshLibraryStats();
    },
    [api, applySettingsPayload, refreshLibraryStats]
  );

  useEffect(() => {
    void refreshLibraryStats();
  }, [refreshLibraryStats, isConnected]);

  useEffect(() => {
    const unsubLibrary = bridgeSocket.on('library_changed', (evt) => {
      void refreshLibraryByEvent(evt?.type);
    });
    return () => {
      unsubLibrary();
    };
  }, [refreshLibraryByEvent]);

  const handleChangeCosmicMode = useCallback(
    (mode: CosmicEffectsMode) => {
      setCosmicMode(mode);
      void api.setSettings({ cosmic_mode: mode, oled_mode: mode === 'oled' }).catch(() => {});
    },
    [api]
  );

  const handleToggleAutoDownloadLiked = useCallback(() => {
    setAutoDownloadLiked((prev) => {
      const next = !prev;
      void api.setSettings({ auto_download_liked: next }).catch(() => {});
      return next;
    });
  }, [api]);

  const handleChangeEqPreset = useCallback(
    (preset: EqPreset) => {
      setEqPreset(preset);
      appliedEqRef.current = preset;
      void api.setEqPreset(preset).catch(() => {});
      void api.setSettings({ eq_preset: preset }).catch(() => {});
    },
    [api]
  );

  const waitForViewportCondition = useCallback(
    (predicate: () => boolean, timeoutMs: number = 140): Promise<void> => {
      return new Promise((resolve) => {
        const deadline = performance.now() + timeoutMs;
        const check = () => {
          if (predicate() || performance.now() >= deadline) {
            requestAnimationFrame(() => resolve());
            return;
          }
          requestAnimationFrame(check);
        };
        requestAnimationFrame(check);
      });
    },
    []
  );

  // Zero-hide, zero-ghost transition into Dynamic Island:
  // 1) Paint 100% transparent frame while window stays visible (WebView2 stays awake).
  // 2) Resize & center transparent HWND (zero stale pixels, DWM transitions disabled).
  // 3) Wait until WebView2 commits <= 420px viewport width, then reveal Dynamic Island.
  const handleEnterIsland = useCallback(async () => {
    if (islandSwitchingRef.current) return;
    islandSwitchingRef.current = true;
    try {
      setIsFullscreenStageOpen(false);
      setRightDrawer('none');
      setSuckPayload(null);
      if (activeTrackRef.current) lastTrackRef.current = activeTrackRef.current;
      setIsWindowTransitioning(true);
      await waitForNextPaint();
      savedBoundsRef.current = await enterDynamicIslandWindow();
      await waitForViewportCondition(() => window.innerWidth <= 420, 140);
      setIsIslandMode(true);
      setIsWindowTransitioning(false);
    } finally {
      islandSwitchingRef.current = false;
    }
  }, [waitForViewportCondition]);

  // Instant, zero-remount transition back to Full App (main shell stays mounted in DOM):
  const handleExitIsland = useCallback(async () => {
    if (islandSwitchingRef.current) return;
    islandSwitchingRef.current = true;
    try {
      setSuckPayload(null);
      if (activeTrackRef.current) lastTrackRef.current = activeTrackRef.current;
      setIsWindowTransitioning(true);
      await exitDynamicIslandWindow(savedBoundsRef.current);
      await waitForViewportCondition(() => window.innerWidth >= 420, 40);
      setRestoreSeq((s) => s + 1);
      setIsIslandMode(false);
      setIsWindowTransitioning(false);
      window.setTimeout(() => void trimProcessMemory(), 900);
    } finally {
      islandSwitchingRef.current = false;
    }
  }, [waitForViewportCondition]);

  const MAX_QUEUE_BATCH = 25;

  const handlePlayTrack = useCallback(
    async (track: Track, index: number = 0, trackList?: Track[]) => {
      setPlayback((prev) => ({
        time_pos: 0,
        duration: track.duration_s,
        state: 'buffering',
        volume: prev ? prev.volume : 80,
        current_track: track,
      }));

      try {
        if (trackList && trackList.length > 0) {
          // Keep queue compact (<= 25 tracks) so clicking a song in a large catalog/history never dumps 150 items
          let batch = trackList;
          let startIdx = Math.max(0, Math.min(index, trackList.length - 1));
          if (trackList.length > MAX_QUEUE_BATCH) {
            const after = trackList.slice(startIdx, startIdx + MAX_QUEUE_BATCH);
            const needed = MAX_QUEUE_BATCH - after.length;
            batch = needed > 0 ? [...after, ...trackList.slice(0, needed)] : after;
            startIdx = 0;
          }
          await api.setQueue(batch, startIdx, true);
        } else {
          await api.play(track);
        }
      } catch {
        // Handled via socket broadcast
      }
    },
    [api, setPlayback]
  );

  const handlePlayAll = useCallback(
    async (tracks: Track[], shuffle: boolean = false) => {
      if (tracks.length === 0) return;
      let batch = tracks;
      if (tracks.length > MAX_QUEUE_BATCH) {
        if (shuffle) {
          const pool = [...tracks];
          for (let i = pool.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [pool[i], pool[j]] = [pool[j], pool[i]];
          }
          batch = pool.slice(0, MAX_QUEUE_BATCH);
        } else {
          batch = tracks.slice(0, MAX_QUEUE_BATCH);
        }
      }
      const startIndex = shuffle && batch.length === tracks.length
        ? Math.floor(Math.random() * batch.length)
        : 0;
      const firstTrack = batch[startIndex];
      setPlayback((prev) => ({
        time_pos: 0,
        duration: firstTrack.duration_s,
        state: 'buffering',
        volume: prev ? prev.volume : 80,
        current_track: firstTrack,
      }));
      setQueue((prev) => (prev ? { ...prev, shuffle } : prev));
      try {
        await api.setShuffle(shuffle);
        await api.setQueue(batch, startIndex, true);
      } catch {
        // Handled via socket broadcast
      }
    },
    [api, setPlayback, setQueue]
  );

  const handleTogglePlay = useCallback(async () => {
    const isPlaying = playback?.state === 'playing' || playback?.state === 'buffering';
    if (!isPlaying && !playback?.current_track && !queue?.current_track) {
      return;
    }
    const prevState = playback?.state;
    setPlayback((prev) =>
      prev ? { ...prev, state: isPlaying ? 'paused' : 'playing' } : null
    );
    try {
      const tick = isPlaying ? await api.pause() : await api.resume();
      if (tick && typeof tick.state === 'string') {
        setPlayback((prev) => ({
          time_pos: typeof tick.time_pos === 'number' ? tick.time_pos : prev?.time_pos ?? 0,
          duration: typeof tick.duration === 'number' ? tick.duration : prev?.duration ?? 0,
          state: tick.state,
          volume: typeof tick.volume === 'number' ? tick.volume : prev?.volume ?? 80,
          current_track:
            tick.current_track !== undefined ? tick.current_track : prev?.current_track ?? null,
        }));
      }
    } catch {
      if (prevState) {
        setPlayback((prev) => (prev ? { ...prev, state: prevState } : null));
      }
    }
  }, [api, playback?.state, playback?.current_track, queue?.current_track, setPlayback]);

  const handleNext = useCallback(async () => {
    if (queue && queue.items.length > 0) {
      const nextIdx =
        queue.repeat_mode === 'one'
          ? queue.current_index
          : queue.current_index + 1 < queue.items.length
          ? queue.current_index + 1
          : queue.repeat_mode === 'all'
          ? 0
          : -1;
      if (nextIdx >= 0) {
        const nextTrack = queue.items[nextIdx];
        setPlayback((prev) => ({
          time_pos: 0,
          duration: nextTrack.duration_s,
          state: 'buffering',
          volume: prev ? prev.volume : 80,
          current_track: nextTrack,
        }));
      }
    }
    try { await api.next(); } catch { /* Ignore */ }
  }, [api, queue, setPlayback]);

  const handlePrev = useCallback(async () => {
    if (queue && queue.items.length > 0) {
      const prevIdx =
        queue.repeat_mode === 'one'
          ? queue.current_index
          : queue.current_index > 0
          ? queue.current_index - 1
          : queue.repeat_mode === 'all'
          ? queue.items.length - 1
          : 0;
      const prevTrack = queue.items[prevIdx];
      if (prevTrack) {
        setPlayback((prev) => ({
          time_pos: 0,
          duration: prevTrack.duration_s,
          state: 'buffering',
          volume: prev ? prev.volume : 80,
          current_track: prevTrack,
        }));
      }
    }
    try { await api.prev(); } catch { /* Ignore */ }
  }, [api, queue, setPlayback]);

  const handleSeek = useCallback(
    async (seconds: number) => {
      bridgeSocket.latestTimePos = seconds;
      setPlayback((prev) => (prev ? { ...prev, time_pos: seconds } : null));
      try { await api.seek(seconds); } catch { /* Ignore */ }
    },
    [api, setPlayback]
  );

  const handleSetVolume = useCallback(
    async (vol: number) => {
      setPlayback((prev) => (prev ? { ...prev, volume: vol } : null));
      try { await api.setVolume(vol); } catch { /* Ignore */ }
    },
    [api, setPlayback]
  );

  const handleToggleShuffle = useCallback(async () => {
    const current = queue?.shuffle ?? false;
    setQueue((prev) => (prev ? { ...prev, shuffle: !current } : null));
    try { await api.setShuffle(!current); } catch { /* Ignore */ }
  }, [api, queue?.shuffle, setQueue]);

  const handleCycleRepeat = useCallback(async () => {
    const modes: ('off' | 'all' | 'one')[] = ['off', 'all', 'one'];
    const current = queue?.repeat_mode || 'off';
    const nextMode = modes[(modes.indexOf(current) + 1) % modes.length];
    setQueue((prev) => (prev ? { ...prev, repeat_mode: nextMode } : null));
    try { await api.setRepeat(nextMode); } catch { /* Ignore */ }
  }, [api, queue?.repeat_mode, setQueue]);

  const handleToggleAutoplayRadio = useCallback(async () => {
    const current = queue?.autoplay_radio ?? true;
    setQueue((prev) => (prev ? { ...prev, autoplay_radio: !current } : null));
    try { await api.setAutoplayRadio(!current); } catch { /* Ignore */ }
  }, [api, queue?.autoplay_radio, setQueue]);

  const handleToggleLike = useCallback(
    async (track: Track) => {
      const currentlyLiked = likedIds.has(track.video_id);
      const nextLiked = !currentlyLiked;

      setLikedIds((prev) => {
        const next = new Set(prev);
        if (nextLiked) next.add(track.video_id);
        else next.delete(track.video_id);
        return next;
      });

      if (nextLiked && autoDownloadLiked && !downloadedIds.has(track.video_id)) {
        setDownloads((prev) => ({
          ...prev,
          [track.video_id]: {
            video_id: track.video_id,
            status: 'queued',
            percent: 0,
            progress_pct: 0,
            downloaded_bytes: 0,
            total_bytes: 0,
          },
        }));
      }

      try {
        await api.toggleLike(track.video_id, track);
        toast.show(
          nextLiked ? `Liked "${track.title}"` : `Removed from Liked`,
          nextLiked ? 'success' : 'info'
        );
      } catch {
        setLikedIds((prev) => {
          const next = new Set(prev);
          if (currentlyLiked) next.add(track.video_id);
          else next.delete(track.video_id);
          return next;
        });
      }
    },
    [api, likedIds, autoDownloadLiked, downloadedIds, setDownloads]
  );

  const handleDownload = useCallback(
    async (track: Track) => {
      setDownloads((prev) => ({
        ...prev,
        [track.video_id]: {
          video_id: track.video_id,
          status: 'queued',
          percent: 0,
          progress_pct: 0,
          downloaded_bytes: 0,
          total_bytes: 0,
        },
      }));
      toast.show(`Download queued: ${track.title}`, 'info');
      try { await api.startDownload(track.video_id, track); } catch { /* Ignore */ }
    },
    [api, setDownloads]
  );

  const handleDeleteDownload = useCallback(
    async (track: Track) => {
      setDownloadTracks((prev) => prev.filter((t) => t.video_id !== track.video_id));
      setDownloads((prev) => {
        const next = { ...prev };
        delete next[track.video_id];
        return next;
      });
      try {
        await api.deleteDownload(track.video_id);
        toast.show('Removed offline download', 'info');
        await refreshLibraryStats();
      } catch {
        await refreshLibraryStats();
      }
    },
    [api, refreshLibraryStats, setDownloads]
  );

  const handleDownloadAll = useCallback(
    async (tracksToDownload: Track[]) => {
      const pending = tracksToDownload.filter((t) => !downloadedIds.has(t.video_id));
      if (pending.length === 0) {
        toast.show('All tracks already downloaded offline', 'info');
        return;
      }
      setDownloads((prev) => {
        const next = { ...prev };
        for (const t of pending) {
          next[t.video_id] = {
            video_id: t.video_id,
            status: 'queued',
            percent: 0,
            progress_pct: 0,
            downloaded_bytes: 0,
            total_bytes: 0,
          };
        }
        return next;
      });
      toast.show(
        pending.length === 1
          ? `Download queued: ${pending[0].title}`
          : `Queued ${pending.length} downloads`,
        'info'
      );
      for (const t of pending) {
        void api.startDownload(t.video_id, t).catch(() => {});
      }
    },
    [api, downloadedIds, setDownloads]
  );

  const handleAddNext = useCallback(async (track: Track) => {
    try {
      await api.addNextQueue(track);
      toast.show(`Playing next: ${track.title}`, 'accent');
    } catch { /* Ignore */ }
  }, [api]);

  const handleAppendQueue = useCallback(async (track: Track) => {
    try {
      await api.appendQueue(track);
      toast.show('Added to queue', 'accent');
    } catch { /* Ignore */ }
  }, [api]);

  const handleRemoveQueueItem = useCallback(async (index: number) => {
    try { await api.removeQueue(index); } catch { /* Ignore */ }
  }, [api]);

  const handleMoveQueueItem = useCallback(async (fromIdx: number, toIdx: number) => {
    try { await api.moveQueue(fromIdx, toIdx); } catch { /* Ignore */ }
  }, [api]);

  const handleClearQueue = useCallback(async () => {
    try {
      await api.clearQueue();
      toast.show('Queue cleared', 'info');
    } catch { /* Ignore */ }
  }, [api]);

  const handleCreatePlaylist = useCallback(
    async (name: string, description: string) => {
      try {
        const created = await api.createPlaylist(name, description);
        setPlaylists((prev) => [...prev, created]);
        toast.show(`Playlist "${created.name}" created`, 'success');
        navigateTo('playlist', created.id);
      } catch { /* Ignore */ }
    },
    [api, navigateTo]
  );

  const handleAddToPlaylist = useCallback(
    async (playlistId: string, track: Track) => {
      try {
        await api.addPlaylistTracks(playlistId, [track]);
        toast.show('Added to playlist', 'success');
        await refreshLibraryStats();
      } catch { /* Ignore */ }
    },
    [api, refreshLibraryStats]
  );

  const handlePlaylistDeleted = useCallback(
    (id: string) => {
      setPlaylists((prev) => prev.filter((p) => p.id !== id));
      navigateTo('home');
    },
    [navigateTo]
  );

  const currentTrackRef = useRef(playback?.current_track);
  currentTrackRef.current = playback?.current_track;

  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement;
      const isInput =
        activeEl?.tagName === 'INPUT' ||
        activeEl?.tagName === 'TEXTAREA' ||
        (activeEl as HTMLElement)?.isContentEditable;

      if (e.code === 'Space' && !isInput) {
        e.preventDefault();
        void handleTogglePlay();
        return;
      }
      if (e.ctrlKey && e.key === 'ArrowRight') {
        e.preventDefault();
        void handleNext();
        return;
      }
      if (e.ctrlKey && e.key === 'ArrowLeft') {
        e.preventDefault();
        void handlePrev();
        return;
      }
      if (e.ctrlKey && e.key.toLowerCase() === 'l') {
        e.preventDefault();
        if (currentTrackRef.current) void handleToggleLike(currentTrackRef.current);
        return;
      }
      if (e.key === 'Escape') {
        if (isUpdateModalOpen) return setIsUpdateModalOpen(false);
        if (isUpdateTabOpen) return setIsUpdateTabOpen(false);
        if (isCreatePlaylistOpen) return setIsCreatePlaylistOpen(false);
        if (addToPlaylistTrack) return setAddToPlaylistTrack(null);
        if (isSettingsOpen) return setIsSettingsOpen(false);
        if (isFullscreenStageOpen) return setIsFullscreenStageOpen(false);
        if (rightDrawer !== 'none') return setRightDrawer('none');
      }
    };

    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, [
    handleTogglePlay,
    handleNext,
    handlePrev,
    handleToggleLike,
    isUpdateModalOpen,
    isUpdateTabOpen,
    isCreatePlaylistOpen,
    addToPlaylistTrack,
    isSettingsOpen,
    isFullscreenStageOpen,
    rightDrawer,
  ]);

  const handleCheckUpdates = useCallback(async () => {
    try {
      const next = await api.checkForUpdates(true);
      setUpdateState(next);
    } catch {
      // Handled via socket or retry
    }
  }, [api, setUpdateState]);

  const handleToggleUpdateTab = useCallback(() => {
    setIsUpdateTabOpen((prev) => {
      const nextOpen = !prev;
      if (nextOpen && (!updateState || updateState.status === 'idle')) {
        void api
          .checkForUpdates(false)
          .then((s) => setUpdateState(s))
          .catch(() => {});
      }
      return nextOpen;
    });
  }, [api, setUpdateState, updateState]);

  const handleCloseUpdateTab = useCallback(() => {
    setIsUpdateTabOpen(false);
  }, []);

  const handleOpenUpdateDownloadTabFromModal = useCallback(() => {
    setIsUpdateModalOpen(false);
    setIsFullscreenStageOpen(false);
    setIsUpdateTabOpen(true);
  }, []);

  const handleDownloadAndInstallUpdate = useCallback(async () => {
    try {
      if (updateState?.status === 'ready' && updateState.downloaded_path) {
        const launched = await launchUpdateInstallerAndQuit(updateState.downloaded_path);
        if (launched) return;
        const next = await api.installUpdate();
        setUpdateState(next);
        return;
      }

      const tauriEnv = isTauri();
      autoInstallPendingRef.current = tauriEnv;
      const next = await api.downloadUpdate(!tauriEnv);
      setUpdateState(next);
    } catch {
      autoInstallPendingRef.current = false;
      // Handled via socket update_status
    }
  }, [api, setUpdateState, updateState?.downloaded_path, updateState?.status]);

  const handleToggleSidebar = useCallback(() => {
    setIsSidebarCollapsed((c) => !c);
  }, []);

  const handleOpenSettings = useCallback(() => {
    setIsSettingsOpen(true);
  }, []);

  const handleOpenCreatePlaylist = useCallback(() => {
    setIsCreatePlaylistOpen(true);
  }, []);

  const handleOpenAddToPlaylist = useCallback((t: Track) => {
    setAddToPlaylistTrack(t);
  }, []);

  const handleCloseRightDrawer = useCallback(() => {
    setRightDrawer('none');
  }, []);

  const handleTitlebarSearchChange = useCallback(
    (q: string) => {
      setSearchQuery(q);
      setSubmittedSearch(q);
      if (q.trim().length > 0) {
        if (activeTab !== 'search') navigateTo('search');
      }
    },
    [activeTab, navigateTo]
  );

  const handleTitlebarSearchSubmit = useCallback(
    (q: string) => {
      setSubmittedSearch(q);
      if (q.trim().length > 0) {
        if (activeTab !== 'search') navigateTo('search');
      }
    },
    [activeTab, navigateTo]
  );

  const handleFocusSearchTab = useCallback(() => {
    setIsFullscreenStageOpen(false);
    if (activeTab !== 'search') navigateTo('search');
  }, [activeTab, navigateTo]);

  const handleOpenFullscreenFromLyrics = useCallback(() => {
    setRightDrawer('none');
    setStageInitialTab('lyrics');
    setIsFullscreenStageOpen(true);
  }, []);

  const handleToggleFullscreenStage = useCallback(() => {
    setIsFullscreenStageOpen((prev) => {
      const next = !prev;
      if (next) {
        setStageInitialTab('lyrics');
        setRightDrawer('none');
      }
      return next;
    });
  }, []);

  const handlePlaySingleHomeTrack = useCallback(
    (t: Track) => {
      void handlePlayTrack(t, 0, [t]);
    },
    [handlePlayTrack]
  );

  const handlePlayQueueItem = useCallback(
    async (track: Track, idx: number) => {
      setPlayback((prev) => ({
        time_pos: 0,
        duration: track.duration_s,
        state: 'buffering',
        volume: prev ? prev.volume : 80,
        current_track: track,
      }));
      try {
        await api.jumpQueue(idx, true);
      } catch {
        // Handled via socket broadcast
      }
    },
    [api, setPlayback]
  );

  const handleEnterIslandAction = useCallback(() => {
    void handleEnterIsland();
  }, [handleEnterIsland]);

  const handleExitIslandAction = useCallback(() => {
    void handleExitIsland();
  }, [handleExitIsland]);

  const handleShellPointerDown = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (activeTab !== 'search') return;
      const target = e.target as HTMLElement | null;
      if (!target) return;
      // Keep search open when interacting with search capsule/panel, nav sidebar, player dock, modals, or buttons
      if (
        target.closest('[data-search-surface="true"]') ||
        target.closest('aside') ||
        target.closest('footer') ||
        target.closest('button') ||
        target.closest('input') ||
        target.closest('textarea') ||
        target.closest('[role="dialog"]')
      ) {
        return;
      }
      handleCloseSearch();
    },
    [activeTab, handleCloseSearch]
  );

  const isLibraryTab =
    activeTab === 'library' ||
    activeTab === 'liked' ||
    activeTab === 'downloads' ||
    activeTab === 'history' ||
    activeTab === 'playlist';

  // Keep LibraryView mounted across Liked/Offline/History/Playlist for instant 1-frame tab switching
  const tabAnimKey = isLibraryTab
    ? 'library-shell'
    : activeTab === 'artist'
    ? `artist:${activeArtist?.channelId || ''}:${activeArtist?.name || ''}`
    : activeTab === 'album'
    ? `album:${activeAlbum?.browseId || ''}:${activeAlbum?.title || ''}`
    : activeTab;

  const hideMainShell = isWindowTransitioning || isIslandMode;

  return (
    <>
      {isWindowTransitioning && (
        <div className="fixed inset-0 w-screen h-screen bg-transparent pointer-events-none z-[100]" />
      )}

      {!isWindowTransitioning && isIslandMode && (
        <DynamicIsland
          playback={playback}
          queue={queue}
          likedIds={likedIds}
          onTogglePlay={handleTogglePlay}
          onNext={handleNext}
          onPrev={handlePrev}
          onSeek={handleSeek}
          onToggleLike={handleToggleLike}
          onExitIsland={handleExitIslandAction}
        />
      )}

      <div
        onMouseDown={handleShellPointerDown}
        className={`relative flex-col h-screen w-screen overflow-hidden select-none text-[#F1F4FA] bg-[#06080F] ${
          hideMainShell ? 'hidden' : 'flex'
        } ${restoreSeq > 0 ? 'eh-app-restore' : ''}`}
      >
      {/* Subtle Top-Right Black Hole + Starfield (paused in Stage or Dynamic Island) */}
      <StarfieldBg
        mode={cosmicMode}
        absorbPulseKey={absorbPulseKey}
        isPlaying={playback?.state === 'playing' || playback?.state === 'buffering'}
        paused={isFullscreenStageOpen || hideMainShell}
      />

      {/* Outgoing Song Card Spiraling into the Black Hole (Full mode only, suppressed when Stage/Island active) */}
      <BlackHoleTransition
        payload={suckPayload}
        enabled={cosmicMode === 'full' && !isIslandMode && !isFullscreenStageOpen}
      />

      <div className="relative z-10 flex flex-col h-full w-full overflow-hidden">
        <Titlebar
          searchQuery={searchQuery}
          onSearchChange={handleTitlebarSearchChange}
          onSearchSubmit={handleTitlebarSearchSubmit}
          canGoBack={historyIndex > 0}
          canGoForward={historyIndex < historyStack.length - 1}
          onGoBack={handleGoBack}
          onGoForward={handleGoForward}
          isConnected={isConnected}
          onOpenSettings={handleOpenSettings}
          onEnterIsland={handleEnterIslandAction}
          onFocusSearchTab={handleFocusSearchTab}
          updateState={updateState}
          isUpdateTabOpen={isUpdateTabOpen}
          onToggleUpdateTab={handleToggleUpdateTab}
          onCloseUpdateTab={handleCloseUpdateTab}
          onCheckUpdates={handleCheckUpdates}
          onDownloadAndInstallUpdate={handleDownloadAndInstallUpdate}
        />

        <div className="flex flex-1 min-h-0 overflow-hidden gap-2 sm:gap-3 px-2 sm:px-3 pb-2 relative">
          <Sidebar
            activeTab={activeTab}
            activePlaylistId={activePlaylistId}
            playlists={playlists}
            likedCount={likedTracks.length}
            downloadCount={downloadTracks.length}
            collapsed={isSidebarCollapsed}
            onToggleCollapse={handleToggleSidebar}
            onSelectTab={navigateTo}
            onOpenCreatePlaylist={handleOpenCreatePlaylist}
          />

          <main
            className="flex-1 min-w-0 flex flex-col overflow-hidden relative bg-transparent"
            style={{ contain: 'layout style' }}
          >
            <div key={tabAnimKey} className="flex-1 min-h-0 flex flex-col eh-tab-enter">
              {activeTab === 'home' && (
                <HomeView
                  currentTrackId={activeVideoId}
                  likedIds={likedIds}
                  onPlayTrack={handlePlaySingleHomeTrack}
                  onPlayAll={handlePlayAll}
                  onToggleLike={handleToggleLike}
                  onAddNext={handleAddNext}
                  onOpenArtist={(name, channelId) => handleOpenArtist({ name, channelId })}
                />
              )}

              {activeTab === 'search' && (
                <SearchView
                  initialQuery={submittedSearch}
                  currentTrackId={activeVideoId}
                  isPlaying={playback?.state === 'playing'}
                  downloads={downloads}
                  likedIds={likedIds}
                  downloadedIds={downloadedIds}
                  onPlay={handlePlayTrack}
                  onToggleLike={handleToggleLike}
                  onDownload={handleDownload}
                  onDeleteDownload={handleDeleteDownload}
                  onAddNext={handleAddNext}
                  onAppendQueue={handleAppendQueue}
                  onOpenAddToPlaylist={handleOpenAddToPlaylist}
                  onOpenArtist={(name, channelId) => handleOpenArtist({ name, channelId })}
                  onOpenAlbum={(albumName, albumId, track) =>
                    handleOpenAlbum({
                      browseId: albumId,
                      title: albumName,
                      artist: track.artist,
                      videoId: track.video_id,
                    })
                  }
                  onCloseSearch={handleCloseSearch}
                />
              )}

              {activeTab === 'artist' && activeArtist && (
                <ArtistView
                  target={activeArtist}
                  currentTrackId={activeVideoId}
                  isPlaying={playback?.state === 'playing'}
                  downloads={downloads}
                  likedIds={likedIds}
                  downloadedIds={downloadedIds}
                  onPlay={handlePlayTrack}
                  onPlayAll={handlePlayAll}
                  onToggleLike={handleToggleLike}
                  onDownload={handleDownload}
                  onDeleteDownload={handleDeleteDownload}
                  onAddNext={handleAddNext}
                  onAppendQueue={handleAppendQueue}
                  onOpenAddToPlaylist={handleOpenAddToPlaylist}
                  onOpenAlbum={handleOpenAlbum}
                  onOpenArtist={handleOpenArtist}
                  onClose={handleGoBack}
                />
              )}

              {activeTab === 'album' && activeAlbum && (
                <AlbumView
                  target={activeAlbum}
                  currentTrackId={activeVideoId}
                  isPlaying={playback?.state === 'playing'}
                  downloads={downloads}
                  likedIds={likedIds}
                  downloadedIds={downloadedIds}
                  onPlay={handlePlayTrack}
                  onPlayAll={handlePlayAll}
                  onToggleLike={handleToggleLike}
                  onDownload={handleDownload}
                  onDeleteDownload={handleDeleteDownload}
                  onDownloadAll={handleDownloadAll}
                  onAddNext={handleAddNext}
                  onAppendQueue={handleAppendQueue}
                  onOpenAddToPlaylist={handleOpenAddToPlaylist}
                  onOpenArtist={handleOpenArtist}
                  onClose={handleGoBack}
                />
              )}

              {isLibraryTab && (
                <LibraryView
                  tab={activeTab}
                  playlistId={activePlaylistId}
                  playlists={playlists}
                  likedTracks={likedTracks}
                  downloadTracks={downloadTracks}
                  currentTrackId={activeVideoId}
                  isPlaying={playback?.state === 'playing'}
                  downloads={downloads}
                  likedIds={likedIds}
                  downloadedIds={downloadedIds}
                  libraryVersion={libraryVersion}
                  onPlay={handlePlayTrack}
                  onPlayAll={handlePlayAll}
                  onToggleLike={handleToggleLike}
                  onDownload={handleDownload}
                  onDeleteDownload={handleDeleteDownload}
                  onDownloadAll={handleDownloadAll}
                  onAddNext={handleAddNext}
                  onAppendQueue={handleAppendQueue}
                  onOpenAddToPlaylist={handleOpenAddToPlaylist}
                  onPlaylistDeleted={handlePlaylistDeleted}
                  onOpenArtist={(name, channelId) => handleOpenArtist({ name, channelId })}
                  onOpenAlbum={(albumName, albumId, track) =>
                    handleOpenAlbum({
                      browseId: albumId,
                      title: albumName,
                      artist: track.artist,
                      videoId: track.video_id,
                    })
                  }
                />
              )}
            </div>
          </main>

          {/* Docked 360px Right Side-Panel Queue (Shifts & resizes main pane cleanly, identical to Lyrics) */}
          <QueueDrawer
            isOpen={rightDrawer === 'queue'}
            queue={queue}
            onClose={handleCloseRightDrawer}
            onPlayQueueItem={handlePlayQueueItem}
            onRemoveQueueItem={handleRemoveQueueItem}
            onMoveQueueItem={handleMoveQueueItem}
            onClearQueue={handleClearQueue}
            onToggleAutoplayRadio={handleToggleAutoplayRadio}
          />

          {/* Docked 360px Right Side-Panel Lyrics (Browse Home/Search/Library alongside live lyrics) */}
          <LyricsDrawer
            isOpen={rightDrawer === 'lyrics'}
            playback={playback}
            queue={queue}
            downloads={downloads}
            likedIds={likedIds}
            downloadedIds={downloadedIds}
            onClose={handleCloseRightDrawer}
            onExpandFullscreen={handleOpenFullscreenFromLyrics}
            onOpenAlbum={handleOpenTrackAlbum}
            onOpenArtist={handleOpenTrackArtist}
            onSeek={handleSeek}
            onToggleLike={handleToggleLike}
            onDownload={handleDownload}
          />
        </div>

        {/* Floating Liquid Glass Player Dock */}
        <div className="px-2 sm:px-4 pb-2 sm:pb-3 pt-0.5 sm:pt-1 flex-none">
          <PlayerBar
            playback={playback}
            queue={queue}
            downloads={downloads}
            likedIds={likedIds}
            downloadedIds={downloadedIds}
            eqPreset={eqPreset}
            isQueueOpen={rightDrawer === 'queue'}
            isLyricsOpen={rightDrawer === 'lyrics'}
            isFullscreenOpen={isFullscreenStageOpen}
            transitionKey={playerRiseKey}
            onChangeEqPreset={handleChangeEqPreset}
            onToggleQueue={() =>
              setRightDrawer((prev) => (prev === 'queue' ? 'none' : 'queue'))
            }
            onToggleLyrics={() =>
              setRightDrawer((prev) => (prev === 'lyrics' ? 'none' : 'lyrics'))
            }
            onToggleFullscreen={handleToggleFullscreenStage}
            onOpenFullLyrics={handleOpenFullscreenFromLyrics}
            onOpenAlbum={handleOpenTrackAlbum}
            onOpenArtist={handleOpenTrackArtist}
            onTogglePlay={handleTogglePlay}
            onNext={handleNext}
            onPrev={handlePrev}
            onSeek={handleSeek}
            onSetVolume={handleSetVolume}
            onToggleShuffle={handleToggleShuffle}
            onCycleRepeat={handleCycleRepeat}
            onToggleLike={handleToggleLike}
            onDownload={handleDownload}
          />
        </div>
      </div>

      <FullscreenStage
        isOpen={isFullscreenStageOpen}
        initialTab={stageInitialTab}
        playback={playback}
        queue={queue}
        downloads={downloads}
        likedIds={likedIds}
        downloadedIds={downloadedIds}
        onClose={() => setIsFullscreenStageOpen(false)}
        onTogglePlay={handleTogglePlay}
        onNext={handleNext}
        onPrev={handlePrev}
        onSeek={handleSeek}
        onToggleShuffle={handleToggleShuffle}
        onCycleRepeat={handleCycleRepeat}
        onToggleLike={handleToggleLike}
        onDownload={handleDownload}
        onPlayQueueItem={handlePlayQueueItem}
        onRemoveQueueItem={handleRemoveQueueItem}
        onMoveQueueItem={handleMoveQueueItem}
        onClearQueue={handleClearQueue}
        onToggleAutoplayRadio={handleToggleAutoplayRadio}
        onOpenAlbum={handleOpenTrackAlbum}
        onOpenArtist={handleOpenTrackArtist}
      />

      <ToastHost />

      <CreatePlaylistModal
        isOpen={isCreatePlaylistOpen}
        onClose={() => setIsCreatePlaylistOpen(false)}
        onCreate={handleCreatePlaylist}
      />

      <AddToPlaylistModal
        isOpen={!!addToPlaylistTrack}
        track={addToPlaylistTrack}
        playlists={playlists}
        onClose={() => setAddToPlaylistTrack(null)}
        onSelectPlaylist={handleAddToPlaylist}
        onOpenCreatePlaylist={() => setIsCreatePlaylistOpen(true)}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        cosmicMode={cosmicMode}
        onChangeCosmicMode={handleChangeCosmicMode}
        autoDownloadLiked={autoDownloadLiked}
        onToggleAutoDownloadLiked={handleToggleAutoDownloadLiked}
      />

      <UpdateAvailableModal
        isOpen={isUpdateModalOpen}
        updateState={updateState}
        onDismiss={() => setIsUpdateModalOpen(false)}
        onOpenDownloadTab={handleOpenUpdateDownloadTabFromModal}
      />

      {visibleError && (
        <div
          role="alert"
          className="fixed bottom-[98px] left-1/2 -translate-x-1/2 z-[90] max-w-md w-[calc(100%-2rem)] ios-liquid-popover eh-popover-in rounded-2xl px-4 py-2.5 flex items-center gap-2.5 border border-[#FF4D8D]/35"
        >
          <AlertCircle size={15} className="text-[#FF4D8D] flex-none" />
          <span className="text-xs text-[#F1F4FA] flex-1 min-w-0 truncate" title={visibleError}>
            {visibleError}
          </span>
          <button
            type="button"
            onClick={() => setDismissedError(visibleError)}
            title="Dismiss"
            className="p-1 text-[#9AA5C4] hover:text-white rounded-full hover:bg-white/[0.08] transition-colors flex-none"
          >
            <X size={13} />
          </button>
        </div>
      )}
      </div>
    </>
  );
}
