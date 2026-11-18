import { useState, useEffect, useCallback, useRef } from 'react';
import { Titlebar } from './components/Titlebar';
import { Sidebar, type NavTab } from './components/Sidebar';
import { PlayerBar } from './components/PlayerBar';
import { QueueDrawer } from './components/QueueDrawer';
import { HomeView } from './views/HomeView';
import { SearchView } from './views/SearchView';
import { LibraryView } from './views/LibraryView';
import {
  CreatePlaylistModal,
  AddToPlaylistModal,
  SettingsModal,
} from './components/Modals';
import { useBridge } from './api/client';
import type { Track, Playlist } from './api/types';

export default function App() {
  const {
    config,
    isConnected,
    playback,
    setPlayback,
    queue,
    setQueue,
    downloads,
    setDownloads,
    api,
  } = useBridge();

  // Navigation state & history stack
  const [activeTab, setActiveTab] = useState<NavTab>('home');
  const [activePlaylistId, setActivePlaylistId] = useState<string | null>(null);
  const [historyStack, setHistoryStack] = useState<{ tab: NavTab; playlistId?: string | null }[]>([
    { tab: 'home' },
  ]);
  const [historyIndex, setHistoryIndex] = useState<number>(0);

  // Search input state
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [submittedSearch, setSubmittedSearch] = useState<string>('');

  // UI Drawer & Modal states
  const [isQueueOpen, setIsQueueOpen] = useState<boolean>(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState<boolean>(false);
  const [isCreatePlaylistOpen, setIsCreatePlaylistOpen] = useState<boolean>(false);
  const [addToPlaylistTrack, setAddToPlaylistTrack] = useState<Track | null>(null);
  const [isOled, setIsOled] = useState<boolean>(false);

  // Playlists & Liked counts
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [likedTracks, setLikedTracks] = useState<Track[]>([]);
  const [downloadTracks, setDownloadTracks] = useState<Track[]>([]);

  // Navigation handlers
  const navigateTo = useCallback((tab: NavTab, playlistId?: string | null) => {
    setActiveTab(tab);
    setActivePlaylistId(playlistId || null);
    setHistoryStack((prev) => {
      const nextStack = prev.slice(0, historyIndex + 1);
      return [...nextStack, { tab, playlistId: playlistId || null }];
    });
    setHistoryIndex((prev) => prev + 1);
  }, [historyIndex]);

  const handleGoBack = useCallback(() => {
    if (historyIndex > 0) {
      const nextIdx = historyIndex - 1;
      const target = historyStack[nextIdx];
      setHistoryIndex(nextIdx);
      setActiveTab(target.tab);
      setActivePlaylistId(target.playlistId || null);
    }
  }, [historyIndex, historyStack]);

  const handleGoForward = useCallback(() => {
    if (historyIndex < historyStack.length - 1) {
      const nextIdx = historyIndex + 1;
      const target = historyStack[nextIdx];
      setHistoryIndex(nextIdx);
      setActiveTab(target.tab);
      setActivePlaylistId(target.playlistId || null);
    }
  }, [historyIndex, historyStack]);

  // Load playlists, liked and downloads counts
  const refreshLibraryStats = useCallback(async () => {
    try {
      const [pls, lk, dl] = await Promise.allSettled([
        api.getPlaylists(),
        api.getLiked(),
        api.getDownloads(),
      ]);
      if (pls.status === 'fulfilled') setPlaylists(pls.value);
      if (lk.status === 'fulfilled') setLikedTracks(lk.value);
      if (dl.status === 'fulfilled') setDownloadTracks(dl.value);
    } catch {
      // Ignore
    }
  }, [api]);

  useEffect(() => {
    refreshLibraryStats();
  }, [refreshLibraryStats, isConnected]);

  // OLED mode DOM attribute toggling
  useEffect(() => {
    if (isOled) {
      document.documentElement.setAttribute('data-theme', 'oled');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  }, [isOled]);

  // ---------------------------------------------------------------------------
  // Audio playback actions (with optimistic updates)
  // ---------------------------------------------------------------------------

  const handlePlayTrack = useCallback(
    async (track: Track, index: number = 0, trackList?: Track[]) => {
      // Optimistic state
      setPlayback((prev) => ({
        time_pos: 0,
        duration: track.duration_s,
        state: 'playing',
        volume: prev ? prev.volume : 80,
        current_track: track,
      }));

      try {
        if (trackList && trackList.length > 0) {
          await api.setQueue(trackList, index, true);
        } else {
          await api.play(track);
        }
      } catch {
        // Handled via socket broadcast or error state
      }
    },
    [api, setPlayback]
  );

  const handlePlayAll = useCallback(
    async (tracks: Track[], shuffle: boolean = false) => {
      if (tracks.length === 0) return;
      const targetList = shuffle ? [...tracks].sort(() => Math.random() - 0.5) : tracks;
      handlePlayTrack(targetList[0], 0, targetList);
      if (shuffle) {
        api.setShuffle(true);
      }
    },
    [api, handlePlayTrack]
  );

  const handleTogglePlay = useCallback(async () => {
    const isPlaying = playback?.state === 'playing';
    setPlayback((prev) =>
      prev ? { ...prev, state: isPlaying ? 'paused' : 'playing' } : null
    );
    try {
      if (isPlaying) {
        await api.pause();
      } else {
        await api.resume();
      }
    } catch {
      // Ignore
    }
  }, [api, playback?.state, setPlayback]);

  const handleNext = useCallback(async () => {
    try {
      await api.next();
    } catch {
      // Ignore
    }
  }, [api]);

  const handlePrev = useCallback(async () => {
    try {
      await api.prev();
    } catch {
      // Ignore
    }
  }, [api]);

  const handleSeek = useCallback(
    async (seconds: number) => {
      setPlayback((prev) => (prev ? { ...prev, time_pos: seconds } : null));
      try {
        await api.seek(seconds);
      } catch {
        // Ignore
      }
    },
    [api, setPlayback]
  );

  const handleSetVolume = useCallback(
    async (vol: number) => {
      setPlayback((prev) => (prev ? { ...prev, volume: vol } : null));
      try {
        await api.setVolume(vol);
      } catch {
        // Ignore
      }
    },
    [api, setPlayback]
  );

  const handleToggleShuffle = useCallback(async () => {
    const current = queue?.shuffle ?? false;
    setQueue((prev) => (prev ? { ...prev, shuffle: !current } : null));
    try {
      await api.setShuffle(!current);
    } catch {
      // Ignore
    }
  }, [api, queue?.shuffle, setQueue]);

  const handleCycleRepeat = useCallback(async () => {
    const modes: ('off' | 'all' | 'one')[] = ['off', 'all', 'one'];
    const current = queue?.repeat_mode || 'off';
    const nextMode = modes[(modes.indexOf(current) + 1) % modes.length];
    setQueue((prev) => (prev ? { ...prev, repeat_mode: nextMode } : null));
    try {
      await api.setRepeat(nextMode);
    } catch {
      // Ignore
    }
  }, [api, queue?.repeat_mode, setQueue]);

  const handleToggleAutoplayRadio = useCallback(async () => {
    const current = queue?.autoplay_radio ?? true;
    setQueue((prev) => (prev ? { ...prev, autoplay_radio: !current } : null));
    try {
      await api.setAutoplayRadio(!current);
    } catch {
      // Ignore
    }
  }, [api, queue?.autoplay_radio, setQueue]);

  const handleToggleLike = useCallback(
    async (track: Track) => {
      const nextLiked = !track.is_liked;

      // Optimistic track update in playback
      if (playback?.current_track?.video_id === track.video_id) {
        setPlayback((prev) =>
          prev && prev.current_track
            ? { ...prev, current_track: { ...prev.current_track, is_liked: nextLiked } }
            : prev
        );
      }

      // Optimistic update in queue
      if (queue?.items) {
        setQueue((prev) =>
          prev
            ? {
                ...prev,
                items: prev.items.map((it) =>
                  it.video_id === track.video_id ? { ...it, is_liked: nextLiked } : it
                ),
              }
            : prev
        );
      }

      try {
        await api.toggleLike(track.video_id, track);
        refreshLibraryStats();
      } catch {
        // Revert on error
      }
    },
    [api, playback?.current_track, queue?.items, refreshLibraryStats, setPlayback, setQueue]
  );

  const handleDownload = useCallback(
    async (track: Track) => {
      setDownloads((prev) => ({
        ...prev,
        [track.video_id]: {
          video_id: track.video_id,
          status: 'queued',
          progress_pct: 0,
          downloaded_bytes: 0,
          total_bytes: 0,
        },
      }));
      try {
        await api.startDownload(track.video_id, track);
        refreshLibraryStats();
      } catch {
        // Ignore
      }
    },
    [api, refreshLibraryStats, setDownloads]
  );

  const handleDownloadAll = useCallback(
    async (tracksToDownload: Track[]) => {
      for (const t of tracksToDownload) {
        handleDownload(t);
      }
    },
    [handleDownload]
  );

  const handleAddNext = useCallback(
    async (track: Track) => {
      try {
        await api.addNextQueue(track);
      } catch {
        // Ignore
      }
    },
    [api]
  );

  const handleAppendQueue = useCallback(
    async (track: Track) => {
      try {
        await api.appendQueue(track);
      } catch {
        // Ignore
      }
    },
    [api]
  );

  const handleRemoveQueueItem = useCallback(
    async (index: number) => {
      try {
        await api.removeQueue(index);
      } catch {
        // Ignore
      }
    },
    [api]
  );

  const handleMoveQueueItem = useCallback(
    async (fromIdx: number, toIdx: number) => {
      try {
        await api.moveQueue(fromIdx, toIdx);
      } catch {
        // Ignore
      }
    },
    [api]
  );

  const handleClearQueue = useCallback(async () => {
    try {
      await api.clearQueue();
    } catch {
      // Ignore
    }
  }, [api]);

  // Playlist creation & track insertion
  const handleCreatePlaylist = useCallback(
    async (name: string, description: string) => {
      try {
        const created = await api.createPlaylist(name, description);
        setPlaylists((prev) => [...prev, created]);
        navigateTo('playlist', created.id);
      } catch {
        // Ignore
      }
    },
    [api, navigateTo]
  );

  const handleAddToPlaylist = useCallback(
    async (playlistId: string, track: Track) => {
      try {
        await api.addPlaylistTracks(playlistId, [track]);
        refreshLibraryStats();
      } catch {
        // Ignore
      }
    },
    [api, refreshLibraryStats]
  );

  const handlePlaylistDeleted = useCallback((id: string) => {
    setPlaylists((prev) => prev.filter((p) => p.id !== id));
    navigateTo('library');
  }, [navigateTo]);

  // Global Keyboard Shortcuts per UI_SPEC.md Section 4
  const currentTrackRef = useRef(playback?.current_track);
  currentTrackRef.current = playback?.current_track;

  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement;
      const isInput =
        activeEl?.tagName === 'INPUT' ||
        activeEl?.tagName === 'TEXTAREA' ||
        (activeEl as HTMLElement)?.isContentEditable;

      // Space: Play / Pause (when not typing in an input)
      if (e.code === 'Space' && !isInput) {
        e.preventDefault();
        handleTogglePlay();
        return;
      }

      // Ctrl+Right: Next track
      if (e.ctrlKey && e.key === 'ArrowRight') {
        e.preventDefault();
        handleNext();
        return;
      }

      // Ctrl+Left: Previous track
      if (e.ctrlKey && e.key === 'ArrowLeft') {
        e.preventDefault();
        handlePrev();
        return;
      }

      // Ctrl+L: Like / Unlike current track
      if (e.ctrlKey && e.key.toLowerCase() === 'l') {
        e.preventDefault();
        if (currentTrackRef.current) {
          handleToggleLike(currentTrackRef.current);
        }
        return;
      }

      // Esc: Close modal or Queue drawer
      if (e.key === 'Escape') {
        if (isCreatePlaylistOpen) {
          setIsCreatePlaylistOpen(false);
          return;
        }
        if (addToPlaylistTrack) {
          setAddToPlaylistTrack(null);
          return;
        }
        if (isSettingsOpen) {
          setIsSettingsOpen(false);
          return;
        }
        if (isQueueOpen) {
          setIsQueueOpen(false);
          return;
        }
      }
    };

    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, [
    handleTogglePlay,
    handleNext,
    handlePrev,
    handleToggleLike,
    isCreatePlaylistOpen,
    addToPlaylistTrack,
    isSettingsOpen,
    isQueueOpen,
  ]);

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden select-none bg-bg-base text-text-1">
      {/* 44px Draggable Frameless Titlebar */}
      <Titlebar
        searchQuery={searchQuery}
        onSearchChange={(q: string) => {
          setSearchQuery(q);
          setSubmittedSearch(q);
          if (activeTab !== 'search') {
            navigateTo('search');
          }
        }}
        onSearchSubmit={(q: string) => {
          setSubmittedSearch(q);
          if (activeTab !== 'search') {
            navigateTo('search');
          }
        }}
        canGoBack={historyIndex > 0}
        canGoForward={historyIndex < historyStack.length - 1}
        onGoBack={handleGoBack}
        onGoForward={handleGoForward}
        isConnected={isConnected}
        onOpenSettings={() => setIsSettingsOpen(true)}
        onFocusSearchTab={() => {
          if (activeTab !== 'search') {
            navigateTo('search');
          }
        }}
      />

      {/* Main Workspace (Sidebar + Dynamic View + Collapsible Queue) */}
      <div className="flex flex-1 min-h-0 overflow-hidden relative">
        {/* 220px Left Sidebar */}
        <Sidebar
          activeTab={activeTab}
          activePlaylistId={activePlaylistId}
          playlists={playlists}
          likedCount={likedTracks.length}
          downloadCount={downloadTracks.length}
          onSelectTab={navigateTo}
          onOpenCreatePlaylist={() => setIsCreatePlaylistOpen(true)}
        />

        {/* Fluid View Area */}
        <main className="flex-1 min-w-0 flex flex-col bg-bg-base overflow-hidden relative">
          {activeTab === 'home' && (
            <HomeView
              onPlayTrack={(t: Track) => handlePlayTrack(t, 0, [t])}
              onPlayAll={handlePlayAll}
            />
          )}

          {activeTab === 'search' && (
            <SearchView
              initialQuery={submittedSearch}
              currentTrackId={playback?.current_track?.video_id}
              isPlaying={playback?.state === 'playing'}
              downloads={downloads}
              onPlay={handlePlayTrack}
              onToggleLike={handleToggleLike}
              onDownload={handleDownload}
              onAddNext={handleAddNext}
              onAppendQueue={handleAppendQueue}
              onOpenAddToPlaylist={(t: Track) => setAddToPlaylistTrack(t)}
            />
          )}

          {(activeTab === 'library' ||
            activeTab === 'liked' ||
            activeTab === 'downloads' ||
            activeTab === 'history' ||
            activeTab === 'playlist') && (
            <LibraryView
              tab={activeTab}
              playlistId={activePlaylistId}
              playlists={playlists}
              currentTrackId={playback?.current_track?.video_id}
              isPlaying={playback?.state === 'playing'}
              downloads={downloads}
              onPlay={handlePlayTrack}
              onPlayAll={handlePlayAll}
              onToggleLike={handleToggleLike}
              onDownload={handleDownload}
              onDownloadAll={handleDownloadAll}
              onAddNext={handleAddNext}
              onAppendQueue={handleAppendQueue}
              onOpenAddToPlaylist={(t: Track) => setAddToPlaylistTrack(t)}
              onPlaylistDeleted={handlePlaylistDeleted}
            />
          )}
        </main>

        {/* 300px Collapsible Queue Drawer */}
        <QueueDrawer
          isOpen={isQueueOpen}
          queue={queue}
          onClose={() => setIsQueueOpen(false)}
          onPlayQueueItem={(track: Track, idx: number) => handlePlayTrack(track, idx, queue?.items)}
          onRemoveQueueItem={handleRemoveQueueItem}
          onMoveQueueItem={handleMoveQueueItem}
          onClearQueue={handleClearQueue}
          onToggleAutoplayRadio={handleToggleAutoplayRadio}
        />
      </div>

      {/* 76px Fixed Bottom Player Bar */}
      <PlayerBar
        playback={playback}
        queue={queue}
        downloads={downloads}
        isQueueOpen={isQueueOpen}
        onToggleQueue={() => setIsQueueOpen((prev) => !prev)}
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

      {/* Modals & Dialogs (No backdrop-filter blur) */}
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
        config={config}
        isOled={isOled}
        onToggleOled={() => setIsOled((prev) => !prev)}
      />
    </div>
  );
}
