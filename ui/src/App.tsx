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
import { forgetHomeHistory, HomeView } from './views/HomeView';
import { SearchView } from './views/SearchView';
import { LibraryView } from './views/LibraryView';
import { ArtistView, type ArtistNavTarget, type AlbumNavTarget } from './views/ArtistView';
import { AlbumView } from './views/AlbumView';
import { ArtistsLibraryView } from './views/ArtistsLibraryView';
import { PlaylistsView } from './views/PlaylistsView';
import { TrackActionsProvider } from './components/TrackContextMenu';
import {
  CreatePlaylistModal,
  AddToPlaylistModal,
  SettingsModal,
  ImportPromoModal,
} from './components/Modals';
import { ImportPlaylistModal } from './components/ImportPlaylistModal';
import { UpdateAvailableModal } from './components/UpdateCenter';
import {
  useBridge,
  bridgeSocket,
  getBackendDiagnostics,
  isTauri,
  enterDynamicIslandWindow,
  exitDynamicIslandWindow,
  closeToTrayWindow,
  consumePendingEnterIsland,
  launchUpdateInstallerAndQuit,
  trimProcessMemory,
  waitForNextPaint,
  type BackendDiagnostics,
  type SavedWindowBounds,
} from './api/client';
import { extractTrackAccent, DEFAULT_COSMIC_ACCENT } from './utils/colorExtract';
import { getTrackArtists } from './utils/artists';
import type { Track, Playlist, EqPreset, PinsResponse, ImportJobState } from './api/types';

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
    beginOptimisticSwitch,
    recoverPlayback,
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
    if (isConnected) {
      setBackendDiag(null);
      return;
    }
    let cancelled = false;
    const pollDiag = () => {
      void getBackendDiagnostics().then((diag) => {
        if (!cancelled && diag) setBackendDiag(diag);
      });
    };
    pollDiag();
    const timer = window.setInterval(pollDiag, 3500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isConnected]);

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
  const searchEdit = useRef<{
    id: string; term: string; queued: string; beforeErase?: string;
  } | null>(null);
  const searchWrites = useRef(Promise.resolve());

  const saveSearchTerm = useCallback((raw: string) => {
    const term = raw.trim();
    const edit = searchEdit.current;
    if (!edit || edit.term !== term || edit.queued === term) return;
    edit.queued = term;
    // Keep writes in typing order, independently of how long result lookups take.
    searchWrites.current = searchWrites.current
      .then(() => api.rememberSearch({ term, edit_id: edit.id }))
      .catch(() => { if (edit.queued === term) edit.queued = ''; });
  }, [api]);

  const finishSearchEdit = useCallback(() => {
    if (searchEdit.current) saveSearchTerm(searchEdit.current.term);
    searchEdit.current = null;
  }, [saveSearchTerm]);

  const updateSearchEdit = useCallback((raw: string, replaceAll = false, previous = '') => {
    const term = raw.trim();
    if (replaceAll) finishSearchEdit();
    const edit = searchEdit.current;
    if (!term) {
      // Backspacing all the way out clears the field; the last single letter isn't a new search.
      if (edit?.beforeErase) edit.term = edit.beforeErase;
      finishSearchEdit();
      return;
    }
    const erasing = !replaceAll && previous.length > term.length && previous.startsWith(term);
    const beforeErase = erasing ? (edit?.beforeErase || previous) : undefined;
    if (edit) {
      edit.term = term;
      edit.beforeErase = beforeErase;
    } else {
      searchEdit.current = { id: crypto.randomUUID(), term, queued: '', beforeErase };
    }
  }, [finishSearchEdit]);

  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(true);
  // Queue and Docked Lyrics share the right-hand slot (mutually exclusive)
  const [rightDrawer, setRightDrawer] = useState<RightDrawerTab>('none');
  const [isFullscreenStageOpen, setIsFullscreenStageOpen] = useState<boolean>(false);
  const [stageInitialTab, setStageInitialTab] = useState<FullscreenTab>('lyrics');
  const [isSettingsOpen, setIsSettingsOpen] = useState<boolean>(false);
  const [backendDiag, setBackendDiag] = useState<BackendDiagnostics | null>(null);
  const [isCreatePlaylistOpen, setIsCreatePlaylistOpen] = useState<boolean>(false);
  const [isImportPlaylistOpen, setIsImportPlaylistOpen] = useState<boolean>(false);
  const [isV14PromoOpen, setIsV14PromoOpen] = useState<boolean>(false);
  const [settingsLoadedOnce, setSettingsLoadedOnce] = useState<boolean>(false);
  const [v14PromoSeen, setV14PromoSeen] = useState<boolean>(false);
  const v14PromoHandledRef = useRef<boolean>(false);
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
  const [lyricsEdgeBlur, setLyricsEdgeBlur] = useState<boolean>(true);
  const [isNarrowViewport, setIsNarrowViewport] = useState<boolean>(
    typeof window !== 'undefined' ? window.innerWidth < 768 : false
  );
  const [eqPreset, setEqPreset] = useState<EqPreset>('flat');
  const appliedEqRef = useRef<EqPreset | null>(null);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);
  const refreshPendingRef = useRef<boolean>(false);

  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [likedTracks, setLikedTracks] = useState<Track[]>([]);
  const [downloadTracks, setDownloadTracks] = useState<Track[]>([]);
  const [downloadTrackMeta, setDownloadTrackMeta] = useState<Record<string, Track>>({});
  const [pins, setPins] = useState<PinsResponse>({ tracks: [], artists: [] });
  const [followedArtistKeys, setFollowedArtistKeys] = useState<Set<string>>(new Set());
  const [isSurpriseBusy, setIsSurpriseBusy] = useState<boolean>(false);
  const surpriseLockRef = useRef<boolean>(false);
  const surpriseCooldownUntilRef = useRef<number>(0);
  const [likedIds, setLikedIds] = useState<Set<string>>(new Set());
  const [libraryVersion, setLibraryVersion] = useState<number>(0);

  const pinnedTrackIds = useMemo(
    () => new Set(pins.tracks.map((t) => t.video_id)),
    [pins.tracks]
  );

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

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)');
    const onChange = () => setIsNarrowViewport(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

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

  // Keep active track and adjacent queue tracks warm in frontend lyrics cache for 0ms switches
  useEffect(() => {
    if (!isConnected) return;
    const targets: Track[] = [];
    if (activeTrack?.video_id) targets.push(activeTrack);
    if (queue && queue.items.length > 0) {
      const idx = queue.current_index ?? 0;
      const nextIdx =
        queue.repeat_mode === 'one'
          ? idx
          : idx + 1 < queue.items.length
          ? idx + 1
          : queue.repeat_mode === 'all'
          ? 0
          : -1;
      const prevIdx =
        idx > 0
          ? idx - 1
          : queue.repeat_mode === 'all'
          ? queue.items.length - 1
          : -1;
      if (nextIdx >= 0 && queue.items[nextIdx]) targets.push(queue.items[nextIdx]);
      if (prevIdx >= 0 && prevIdx !== nextIdx && queue.items[prevIdx]) {
        targets.push(queue.items[prevIdx]);
      }
    }
    const seen = new Set<string>();
    for (const t of targets) {
      if (t.video_id && !seen.has(t.video_id)) {
        seen.add(t.video_id);
        api.prefetchLyrics(t);
      }
    }
  }, [api, isConnected, activeTrack, queue]);

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
      const cleaned = (target.name || '').trim();
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
    (track: Track, artistName?: string, artistId?: string | null) => {
      if (artistName && artistName.trim()) {
        handleOpenArtist({
          name: artistName.trim(),
          channelId: artistId ?? null,
        });
        return;
      }
      const first = getTrackArtists(track)[0];
      handleOpenArtist({
        name: first?.name || track.artist,
        channelId: first?.channelId ?? track.artist_id ?? null,
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
    finishSearchEdit();
    setSearchQuery('');
    setSubmittedSearch('');
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    if (activeTab === 'search') {
      navigateTo('home');
    }
  }, [activeTab, navigateTo, finishSearchEdit]);

  const applySettingsPayload = useCallback(
    (settingsObj: Record<string, unknown> | null | undefined) => {
      if (!settingsObj) return;
      if (settingsObj.import_promo_v1_4_seen === true) {
        v14PromoHandledRef.current = true;
        setV14PromoSeen(true);
      }
      setSettingsLoadedOnce(true);
      if (typeof settingsObj.auto_download_liked === 'boolean') {
        setAutoDownloadLiked(settingsObj.auto_download_liked);
      }
      if (typeof settingsObj.lyrics_edge_blur === 'boolean') {
        setLyricsEdgeBlur(settingsObj.lyrics_edge_blur);
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

  const refreshPinsOnly = useCallback(async () => {
    try {
      const p = await api.getPins();
      setPins(p);
    } catch {
      // Ignore
    }
  }, [api]);

  const refreshFollowedArtistsOnly = useCallback(async () => {
    try {
      const res = await api.getArtistsLibrary();
      const nextKeys = new Set<string>();
      for (const a of res.followed || []) {
        if (a.channel_id) nextKeys.add(a.channel_id);
        if (a.artist_norm) nextKeys.add(`norm:${a.artist_norm}`);
      }
      setFollowedArtistKeys(nextKeys);
    } catch {
      // Ignore
    }
  }, [api]);

  const refreshLibraryStats = useCallback(async () => {
    if (refreshInFlightRef.current) {
      refreshPendingRef.current = true;
      return refreshInFlightRef.current;
    }

    const run = (async () => {
      try {
        const [pls, lk, dl, settings, pinsRes, artistsRes] = await Promise.allSettled([
          api.getPlaylists(),
          api.getLiked(),
          api.getDownloads(),
          api.getSettings(),
          api.getPins(),
          api.getArtistsLibrary(),
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
        if (pinsRes.status === 'fulfilled') {
          setPins(pinsRes.value);
        }
        if (artistsRes.status === 'fulfilled') {
          const nextKeys = new Set<string>();
          for (const a of artistsRes.value.followed || []) {
            if (a.channel_id) nextKeys.add(a.channel_id);
            if (a.artist_norm) nextKeys.add(`norm:${a.artist_norm}`);
          }
          setFollowedArtistKeys(nextKeys);
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
        if (evtType === 'pins_updated') {
          await refreshPinsOnly();
          return;
        }
        if (evtType === 'artist_followed' || evtType === 'artist_unfollowed') {
          await refreshFollowedArtistsOnly();
          setLibraryVersion((v) => v + 1);
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
    [api, applySettingsPayload, refreshLibraryStats, refreshPinsOnly, refreshFollowedArtistsOnly]
  );

  useEffect(() => {
    void refreshLibraryStats();
  }, [refreshLibraryStats, isConnected]);

  useEffect(() => {
    const unsubLibrary = bridgeSocket.on('library_changed', (evt) => {
      if (evt?.type === 'history_cleared') forgetHomeHistory();
      void refreshLibraryByEvent(evt?.type);
    });
    return () => {
      unsubLibrary();
    };
  }, [refreshLibraryByEvent]);

  // One-time v1.4.x playlist importer prompt for fresh website installs and in-app upgrades.
  // Intentionally gated strictly to 1.4.x so future versions (1.5+) never show this popup.
  useEffect(() => {
    if (!isConnected || !settingsLoadedOnce || v14PromoHandledRef.current || v14PromoSeen) {
      return;
    }
    if (isUpdateModalOpen) {
      return;
    }
    const currentVer = (updateState?.current_version ?? '').trim();
    const isV14Release = /^v?1\.4(\.|$)/i.test(currentVer);
    const devPreview =
      import.meta.env.DEV &&
      typeof window !== 'undefined' &&
      new URLSearchParams(window.location.search).get('test_v14_promo') === '1';

    if (!isV14Release && !devPreview) {
      return;
    }

    v14PromoHandledRef.current = true;
    setV14PromoSeen(true);
    setIsV14PromoOpen(true);
    void api.setSettings({ import_promo_v1_4_seen: true }).catch(() => {});
  }, [
    isConnected,
    settingsLoadedOnce,
    v14PromoSeen,
    updateState?.current_version,
    isUpdateModalOpen,
    api,
  ]);

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

  const handleToggleLyricsEdgeBlur = useCallback(() => {
    setLyricsEdgeBlur((prev) => {
      const next = !prev;
      void api.setSettings({ lyrics_edge_blur: next }).catch(() => {});
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

  const handlePlayTrack = useCallback(
    async (track: Track, index: number = 0, trackList?: Track[]) => {
      const sequence = beginOptimisticSwitch(track);
      setPlayback((prev) => ({
        time_pos: 0,
        duration: track.duration_s,
        state: 'buffering',
        volume: prev ? prev.volume : 80,
        current_track: track,
      }));

      try {
        if (trackList && trackList.length > 0) {
          await api.setQueue(trackList, index, true);
        } else {
          await api.play(track);
        }
        return true;
      } catch {
        await recoverPlayback(sequence);
        return false;
      }
    },
    [api, beginOptimisticSwitch, recoverPlayback, setPlayback]
  );

  const handlePlayAll = useCallback(
    async (tracks: Track[], shuffle: boolean = false) => {
      if (tracks.length === 0) return;
      const startIndex = shuffle ? Math.floor(Math.random() * tracks.length) : 0;
      const firstTrack = tracks[startIndex];
      const sequence = beginOptimisticSwitch(firstTrack);
      setPlayback((prev) => ({
        time_pos: 0,
        duration: firstTrack.duration_s,
        state: 'buffering',
        volume: prev ? prev.volume : 80,
        current_track: firstTrack,
      }));
      setQueue((prev) => (prev ? { ...prev, shuffle } : prev));
      try {
        await api.setQueue(tracks, startIndex, true, shuffle);
      } catch {
        await recoverPlayback(sequence);
      }
    },
    [api, beginOptimisticSwitch, recoverPlayback, setPlayback, setQueue]
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
    let sequence = beginOptimisticSwitch(null);
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
        sequence = beginOptimisticSwitch(nextTrack);
        setPlayback((prev) => ({
          time_pos: 0,
          duration: nextTrack.duration_s,
          state: 'buffering',
          volume: prev ? prev.volume : 80,
          current_track: nextTrack,
        }));
        setQueue((prev) =>
          prev ? { ...prev, current_index: nextIdx, current_track: nextTrack } : prev
        );
      }
    }
    try {
      await api.next();
    } catch {
      await recoverPlayback(sequence);
    }
  }, [api, beginOptimisticSwitch, recoverPlayback, queue, setPlayback, setQueue]);

  const handlePrev = useCallback(async () => {
    let sequence = beginOptimisticSwitch(null);
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
        sequence = beginOptimisticSwitch(prevTrack);
        setPlayback((prev) => ({
          time_pos: 0,
          duration: prevTrack.duration_s,
          state: 'buffering',
          volume: prev ? prev.volume : 80,
          current_track: prevTrack,
        }));
        setQueue((prev) =>
          prev ? { ...prev, current_index: prevIdx, current_track: prevTrack } : prev
        );
      }
    }
    try {
      await api.prev();
    } catch {
      await recoverPlayback(sequence);
    }
  }, [api, beginOptimisticSwitch, recoverPlayback, queue, setPlayback, setQueue]);

  const handleSeek = useCallback(
    async (seconds: number) => {
      bridgeSocket.latestTimePos = seconds;
      bridgeSocket.latestTickAt = performance.now();
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
        if (nextLiked && autoDownloadLiked && !downloadedIds.has(track.video_id)) {
          setDownloads(prev => {
            if (prev[track.video_id]?.status !== 'queued') return prev;
            const next = { ...prev };
            delete next[track.video_id];
            return next;
          });
        }
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

  const submitDownload = useCallback(async (videoId: string, track?: Track) => {
    try {
      await api.startDownload(videoId, track);
    } catch {
      setDownloads(prev => prev[videoId]?.status === 'queued' ? {
        ...prev, [videoId]: { ...prev[videoId], status: 'error', error: 'Could not queue download. Retry.' },
      } : prev);
    }
  }, [api, setDownloads]);

  const handleDownload = useCallback(
    async (track: Track) => {
      setDownloadTrackMeta((prev) => ({ ...prev, [track.video_id]: track }));
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
      await submitDownload(track.video_id, track);
    },
    [submitDownload, setDownloads]
  );

  const handleRetryDownload = useCallback(
    async (videoId: string) => {
      const meta = downloadTrackMeta[videoId];
      setDownloads((prev) => ({
        ...prev,
        [videoId]: {
          video_id: videoId,
          status: 'queued',
          percent: 0,
          progress_pct: 0,
          downloaded_bytes: 0,
          total_bytes: 0,
        },
      }));
      await submitDownload(videoId, meta);
    },
    [submitDownload, downloadTrackMeta, setDownloads]
  );

  const handleCancelDownload = useCallback(
    async (videoId: string) => {
      setDownloads((prev) => {
        const next = { ...prev };
        delete next[videoId];
        return next;
      });
      try {
        await api.deleteDownload(videoId);
        toast.show('Download canceled', 'info');
      } catch { /* Ignore */ }
    },
    [api, setDownloads]
  );

  const handleClearFinishedDownloads = useCallback(
    (videoIds: string[]) => {
      setDownloads((prev) => {
        const next = { ...prev };
        for (const vid of videoIds) {
          delete next[vid];
        }
        return next;
      });
    },
    [setDownloads]
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
      setDownloadTrackMeta((prev) => {
        const next = { ...prev };
        for (const t of pending) next[t.video_id] = t;
        return next;
      });
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
        void submitDownload(t.video_id, t);
      }
    },
    [submitDownload, downloadedIds, setDownloads]
  );

  const handleAddNext = useCallback(async (track: Track) => {
    try {
      await api.addNextQueue(track);
      toast.show(`Playing next: ${track.title}`, 'accent');
      return true;
    } catch { return false; }
  }, [api]);

  const handleAppendQueue = useCallback(async (track: Track) => {
    try {
      await api.appendQueue(track);
      toast.show('Added to queue', 'accent');
      return true;
    } catch { return false; }
  }, [api]);

  const handleRemoveQueueItem = useCallback(async (index: number) => {
    try { await api.removeQueue(index); } catch { /* Ignore */ }
  }, [api]);

  const handleMoveQueueItem = useCallback(
    async (fromIdx: number, toIdx: number, targetSource?: 'user' | 'radio') => {
      try {
        await api.moveQueue(fromIdx, toIdx, targetSource);
      } catch { /* Ignore */ }
    },
    [api]
  );

  const handlePromoteQueueItem = useCallback(async (index: number) => {
    try {
      await api.promoteQueue(index);
      toast.show('Moved into active queue', 'accent');
    } catch { /* Ignore */ }
  }, [api]);

  const handleClearQueue = useCallback(async () => {
    try {
      await api.clearQueue();
      toast.show('Queue cleared', 'info');
    } catch { /* Ignore */ }
  }, [api]);

  const normalizeArtistKey = (name?: string | null) =>
    (name || '').trim().toLowerCase().replace(/\s+/g, ' ');

  const isArtistFollowedGlobal = useCallback(
    (name?: string | null, channelId?: string | null): boolean => {
      if (channelId && followedArtistKeys.has(channelId.trim())) return true;
      const norm = normalizeArtistKey(name);
      if (norm && followedArtistKeys.has(`norm:${norm}`)) return true;
      return false;
    },
    [followedArtistKeys]
  );

  const handleToggleFollowArtist = useCallback(
    async (artist: { name: string; channelId?: string | null; thumbnailUrl?: string | null }) => {
      const cleanName = artist.name.trim();
      const norm = normalizeArtistKey(cleanName);
      const cid = artist.channelId?.trim() || null;
      const currentlyFollowed = isArtistFollowedGlobal(cleanName, cid);
      const next = !currentlyFollowed;

      setFollowedArtistKeys((prev) => {
        const updated = new Set(prev);
        if (next) {
          if (cid) updated.add(cid);
          if (norm) updated.add(`norm:${norm}`);
        } else {
          if (cid) updated.delete(cid);
          if (norm) updated.delete(`norm:${norm}`);
        }
        return updated;
      });

      try {
        if (next) {
          const entry = await api.followArtist({
            channel_id: cid,
            name: cleanName,
            thumbnail_url: artist.thumbnailUrl ?? null,
          });
          setFollowedArtistKeys((prev) => {
            const updated = new Set(prev);
            if (entry.channel_id) updated.add(entry.channel_id);
            if (entry.artist_norm) updated.add(`norm:${entry.artist_norm}`);
            return updated;
          });
          toast.show(`Following ${cleanName}`, 'success');
        } else {
          await api.unfollowArtist(cid || cleanName);
          toast.show(`Unfollowed ${cleanName}`, 'info');
        }
        setLibraryVersion((v) => v + 1);
        return next;
      } catch {
        await refreshFollowedArtistsOnly();
        return currentlyFollowed;
      }
    },
    [api, isArtistFollowedGlobal, refreshFollowedArtistsOnly]
  );

  const handlePinToggle = useCallback(
    async (track: Track) => {
      try {
        if (pinnedTrackIds.has(track.video_id)) {
          await api.unpinTrack(track.video_id);
          setPins((prev) => ({
            ...prev,
            tracks: prev.tracks.filter((t) => t.video_id !== track.video_id),
          }));
          toast.show(`Unpinned "${track.title}"`, 'info');
        } else {
          const updated = await api.pinTrack(track.video_id, track);
          setPins(updated);
          toast.show(`Pinned "${track.title}" to Speed Dial`, 'success');
        }
      } catch { /* Ignore */ }
    },
    [api, pinnedTrackIds]
  );

  const handleUnpinTrack = useCallback(
    async (videoId: string) => {
      setPins((prev) => ({
        ...prev,
        tracks: prev.tracks.filter((t) => t.video_id !== videoId),
      }));
      try {
        await api.unpinTrack(videoId);
      } catch {
        await refreshPinsOnly();
      }
    },
    [api, refreshPinsOnly]
  );

  const handleUnpinArtist = useCallback(
    async (channelId: string) => {
      setPins((prev) => ({
        ...prev,
        artists: prev.artists.filter((a) => a.channel_id !== channelId),
      }));
      try {
        await api.unpinArtist(channelId);
      } catch {
        await refreshPinsOnly();
      }
    },
    [api, refreshPinsOnly]
  );

  const handleStartRadio = useCallback(
    async (track: Track) => {
      toast.show(`Starting radio from ${track.title}...`, 'accent');
      try {
        await api.startSurpriseRadio(track.video_id);
      } catch {
        toast.show('Unable to start radio right now', 'info');
      }
    },
    [api]
  );

  const handleSurpriseRadio = useCallback(async () => {
    const now = performance.now();
    if (surpriseLockRef.current || now < surpriseCooldownUntilRef.current) {
      return;
    }
    surpriseLockRef.current = true;
    setIsSurpriseBusy(true);
    toast.show('Discovering fresh Surprise Mix...', 'accent');
    try {
      await api.startSurpriseRadio();
    } catch {
      toast.show('Unable to launch Surprise Mix', 'info');
    } finally {
      surpriseCooldownUntilRef.current = performance.now() + 900;
      surpriseLockRef.current = false;
      setIsSurpriseBusy(false);
    }
  }, [api]);

  const handleShareTrack = useCallback((track: Track) => {
    const url = `https://music.youtube.com/watch?v=${track.video_id}`;
    void navigator.clipboard?.writeText(url);
    toast.show('Copied track link to clipboard', 'success');
  }, []);

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
      if (activeTab === 'playlist' && activePlaylistId === id) {
        navigateTo('playlists');
      }
    },
    [activePlaylistId, activeTab, navigateTo]
  );

  const handleOpenImportPlaylist = useCallback(() => {
    setIsImportPlaylistOpen(true);
  }, []);

  const handleDismissV14Promo = useCallback(() => {
    v14PromoHandledRef.current = true;
    setV14PromoSeen(true);
    setIsV14PromoOpen(false);
    void api.setSettings({ import_promo_v1_4_seen: true }).catch(() => {});
  }, [api]);

  const handleAcceptV14Promo = useCallback(() => {
    v14PromoHandledRef.current = true;
    setV14PromoSeen(true);
    setIsV14PromoOpen(false);
    void api.setSettings({ import_promo_v1_4_seen: true }).catch(() => {});
    setIsImportPlaylistOpen(true);
  }, [api]);

  const handleImportComplete = useCallback(
    (job: ImportJobState) => {
      toast.show(`Imported ${job.matched} / ${job.total} tracks into "${job.playlist_name}"`, 'success');
      void refreshLibraryStats();
    },
    [refreshLibraryStats]
  );

  const currentTrackRef = useRef(playback?.current_track);
  currentTrackRef.current = playback?.current_track;

  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if (document.querySelector('dialog[open]')) return;
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
        if (isV14PromoOpen) return handleDismissV14Promo();
        if (isImportPlaylistOpen) return setIsImportPlaylistOpen(false);
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
    handleDismissV14Promo,
    isUpdateModalOpen,
    isUpdateTabOpen,
    isV14PromoOpen,
    isImportPlaylistOpen,
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
    const nextOpen = !isUpdateTabOpen;
    setIsUpdateTabOpen(nextOpen);
    if (
      nextOpen &&
      (!updateState || updateState.status === 'idle' || updateState.status === 'error')
    ) {
      void api
        .checkForUpdates(false)
        .then((s) => setUpdateState(s))
        .catch(() => {});
    }
  }, [api, isUpdateTabOpen, setUpdateState, updateState]);

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
    (q: string, replaceAll = false) => {
      updateSearchEdit(q, replaceAll, searchQuery.trim());
      setSearchQuery(q);
      setSubmittedSearch(q);
      if (q.trim().length > 0) {
        if (activeTab !== 'search') navigateTo('search');
      }
    },
    [activeTab, navigateTo, searchQuery, updateSearchEdit]
  );

  const handleTitlebarSearchSubmit = useCallback(
    (q: string) => {
      setSearchQuery(q);
      setSubmittedSearch(q);
      if (q.trim().length > 0) {
        updateSearchEdit(q);
        finishSearchEdit();
        if (activeTab !== 'search') navigateTo('search');
      }
    },
    [activeTab, finishSearchEdit, navigateTo, updateSearchEdit]
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
      const sequence = beginOptimisticSwitch(track);
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
        await recoverPlayback(sequence);
      }
    },
    [api, beginOptimisticSwitch, recoverPlayback, setPlayback]
  );

  const handleEnterIslandAction = useCallback(() => {
    void handleEnterIsland();
  }, [handleEnterIsland]);

  const handleExitIslandAction = useCallback(() => {
    void handleExitIsland();
  }, [handleExitIsland]);

  const handleCloseIslandToTrayAction = useCallback(() => {
    void closeToTrayWindow();
  }, []);

  // Allow System Tray popup to launch or exit Dynamic Island directly
  useEffect(() => {
    if (!isTauri()) return;
    let active = true;
    const unlisteners: (() => void)[] = [];

    void consumePendingEnterIsland().then((shouldEnter) => {
      if (active && shouldEnter && !isIslandModeRef.current) {
        void handleEnterIsland();
      }
    });

    void import('@tauri-apps/api/event')
      .then(({ listen }) =>
        Promise.all([
          listen('orbit://enter-island', () => {
            if (active && !isIslandModeRef.current) {
              void handleEnterIsland();
            }
          }),
          listen('orbit://exit-island', () => {
            if (active && isIslandModeRef.current) {
              void handleExitIsland();
            }
          }),
        ])
      )
      .then((fns) => {
        if (active) unlisteners.push(...fns);
        else fns.forEach((fn) => fn());
      })
      .catch(() => {});

    return () => {
      active = false;
      unlisteners.forEach((fn) => fn());
    };
  }, [handleEnterIsland, handleExitIsland]);

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

  const trackActionsContextValue = useMemo(
    () => ({
      pinnedTrackIds,
      followedArtistKeys,
      isArtistFollowed: isArtistFollowedGlobal,
      onToggleFollowArtist: handleToggleFollowArtist,
      onPinToggle: handlePinToggle,
      onStartRadio: handleStartRadio,
      onShareTrack: handleShareTrack,
      onOpenAddToPlaylist: handleOpenAddToPlaylist,
    }),
    [
      pinnedTrackIds,
      followedArtistKeys,
      isArtistFollowedGlobal,
      handleToggleFollowArtist,
      handlePinToggle,
      handleStartRadio,
      handleShareTrack,
      handleOpenAddToPlaylist,
    ]
  );

  const hideMainShell = isWindowTransitioning || isIslandMode;

  return (
    <TrackActionsProvider value={trackActionsContextValue}>
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
          onCloseToTray={handleCloseIslandToTrayAction}
        />
      )}

      <div
        onMouseDown={handleShellPointerDown}
        className={`relative flex-col h-screen w-screen overflow-hidden select-none text-[#F1F4FA] bg-[#06080F] ${
          hideMainShell ? 'hidden' : 'flex'
        } ${restoreSeq > 0 ? 'eh-app-restore' : ''}`}
      >
      {/* Subtle Top-Right Black Hole + Starfield (paused in Stage, full-pane Lyrics, or Dynamic Island) */}
      <StarfieldBg
        mode={cosmicMode}
        absorbPulseKey={absorbPulseKey}
        isPlaying={playback?.state === 'playing' || playback?.state === 'buffering'}
        paused={isFullscreenStageOpen || hideMainShell || (rightDrawer === 'lyrics' && isNarrowViewport)}
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
          onSearchBlur={finishSearchEdit}
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
          downloads={downloads}
          downloadTracks={downloadTrackMeta}
          onRetryDownload={handleRetryDownload}
          onCancelDownload={handleCancelDownload}
          onClearFinishedDownloads={handleClearFinishedDownloads}
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
            onOpenImportPlaylist={handleOpenImportPlaylist}
          />

          <div className="orbit-workspace relative flex flex-1 min-w-0 min-h-0 gap-2 sm:gap-3">
          <main
            className="orbit-content flex-1 min-w-0 flex flex-col overflow-hidden relative bg-transparent"
            style={{ contain: 'layout style' }}
          >
            <div key={tabAnimKey} className="flex-1 min-h-0 flex flex-col eh-tab-enter">
              {activeTab === 'home' && (
                <HomeView
                  currentTrackId={activeVideoId}
                  isConnected={isConnected}
                  backendDiag={backendDiag}
                  likedIds={likedIds}
                  downloads={downloads}
                  downloadedIds={downloadedIds}
                  pins={pins}
                  libraryVersion={libraryVersion}
                  surpriseLoading={isSurpriseBusy}
                  onPlayTrack={handlePlaySingleHomeTrack}
                  onPlayAll={handlePlayAll}
                  onToggleLike={handleToggleLike}
                  onAddNext={handleAddNext}
                  onAppendQueue={handleAppendQueue}
                  onOpenAddToPlaylist={handleOpenAddToPlaylist}
                  onDownload={handleDownload}
                  onDeleteDownload={handleDeleteDownload}
                  onUnpinTrack={handleUnpinTrack}
                  onUnpinArtist={handleUnpinArtist}
                  onSurpriseRadio={handleSurpriseRadio}
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

              {activeTab === 'search' && (
                <SearchView
                  initialQuery={submittedSearch}
                  onSelectTerm={handleTitlebarSearchSubmit}
                  onQueryRun={saveSearchTerm}
                  isConnected={isConnected}
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
                  onArtistFollowChanged={() => setLibraryVersion((v) => v + 1)}
                  onArtistPinChanged={() => void refreshPinsOnly()}
                  onArtistRadioStarted={() => setRightDrawer('queue')}
                />
              )}

              {activeTab === 'artists' && (
                <ArtistsLibraryView
                  libraryVersion={libraryVersion}
                  onOpenArtist={(name, channelId) => handleOpenArtist({ name, channelId })}
                  onArtistChanged={() => setLibraryVersion((v) => v + 1)}
                />
              )}

              {activeTab === 'playlists' && (
                <PlaylistsView
                  playlists={playlists}
                  onOpenPlaylist={(playlistId) => navigateTo('playlist', playlistId)}
                  onCreatePlaylist={handleOpenCreatePlaylist}
                  onImportPlaylist={handleOpenImportPlaylist}
                  onPlaylistDeleted={handlePlaylistDeleted}
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
            downloads={downloads}
            downloadedIds={downloadedIds}
            onClose={handleCloseRightDrawer}
            onPlayQueueItem={handlePlayQueueItem}
            onRemoveQueueItem={handleRemoveQueueItem}
            onMoveQueueItem={handleMoveQueueItem}
            onPromoteQueueItem={handlePromoteQueueItem}
            onClearQueue={handleClearQueue}
            onToggleAutoplayRadio={handleToggleAutoplayRadio}
            onToggleShuffle={handleToggleShuffle}
            onCycleRepeat={handleCycleRepeat}
            onAddNext={handleAddNext}
            onAppendQueue={handleAppendQueue}
            onOpenArtist={(name, channelId) => handleOpenArtist({ name, channelId })}
            onOpenAlbum={(albumName, albumId, track) =>
              handleOpenAlbum({
                browseId: albumId,
                title: albumName,
                artist: track.artist,
                videoId: track.video_id,
              })
            }
            onOpenAddToPlaylist={handleOpenAddToPlaylist}
            onDownload={handleDownload}
            onDeleteDownload={handleDeleteDownload}
          />

          {/* Docked 360px Right Side-Panel Lyrics (Browse Home/Search/Library alongside live lyrics) */}
          <LyricsDrawer
            isOpen={rightDrawer === 'lyrics'}
            playback={playback}
            queue={queue}
            downloads={downloads}
            likedIds={likedIds}
            downloadedIds={downloadedIds}
            lyricsEdgeBlur={lyricsEdgeBlur}
            onClose={handleCloseRightDrawer}
            onExpandFullscreen={handleOpenFullscreenFromLyrics}
            onOpenAlbum={handleOpenTrackAlbum}
            onOpenArtist={handleOpenTrackArtist}
            onSeek={handleSeek}
            onToggleLike={handleToggleLike}
            onDownload={handleDownload}
          />
          </div>
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
        lyricsEdgeBlur={lyricsEdgeBlur}
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
        onPromoteQueueItem={handlePromoteQueueItem}
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

      <ImportPlaylistModal
        isOpen={isImportPlaylistOpen}
        playlists={playlists}
        onClose={() => setIsImportPlaylistOpen(false)}
        onOpenPlaylist={(playlistId) => navigateTo('playlist', playlistId)}
        onOpenLiked={() => navigateTo('liked')}
        onImportComplete={handleImportComplete}
      />

      <ImportPromoModal
        isOpen={isV14PromoOpen}
        onDismiss={handleDismissV14Promo}
        onStartImport={handleAcceptV14Promo}
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
        lyricsEdgeBlur={lyricsEdgeBlur}
        onToggleLyricsEdgeBlur={handleToggleLyricsEdgeBlur}
        onPrivacyChanged={() => setLibraryVersion((v) => v + 1)}
        onOpenImportPlaylist={handleOpenImportPlaylist}
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
    </TrackActionsProvider>
  );
}
