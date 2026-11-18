import {
  X,
  Trash2,
  Radio,
  ArrowUp,
  ArrowDown,
  Play,
  Music,
} from 'lucide-react';
import type { QueueData, Track } from '../api/types';

interface QueueDrawerProps {
  isOpen: boolean;
  queue: QueueData | null;
  onClose: () => void;
  onPlayQueueItem: (track: Track, index: number) => void;
  onRemoveQueueItem: (index: number) => void;
  onMoveQueueItem: (fromIdx: number, toIdx: number) => void;
  onClearQueue: () => void;
  onToggleAutoplayRadio: () => void;
}

export const QueueDrawer = ({
  isOpen,
  queue,
  onClose,
  onPlayQueueItem,
  onRemoveQueueItem,
  onMoveQueueItem,
  onClearQueue,
  onToggleAutoplayRadio,
}: QueueDrawerProps) => {
  if (!isOpen) return null;

  const currentTrack = queue?.current_track || null;
  const currentIndex = queue?.current_index ?? 0;
  const items = queue?.items || [];
  const autoplayRadio = queue?.autoplay_radio ?? true;

  const formatTime = (secs?: number): string => {
    if (!secs || isNaN(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const formatThumb = (url?: string | null): string | undefined => {
    if (!url) return undefined;
    if (url.includes('googleusercontent.com') || url.includes('ggpht.com')) {
      return url.replace(/=w\d+-h\d+/, '=w120-h120');
    }
    return url;
  };

  return (
    <aside className="w-[300px] flex-none bg-bg-surface border-l border-border-subtle flex flex-col z-40 transition-smooth select-none overflow-hidden">
      {/* Header */}
      <div className="h-12 flex-none px-4 flex items-center justify-between border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold uppercase tracking-wider text-text-1">Queue</span>
          <span className="text-[10px] font-mono text-text-2 bg-bg-base px-1.5 py-0.2 rounded border border-border-subtle">
            {items.length}
          </span>
        </div>

        <div className="flex items-center gap-1">
          {items.length > 0 && (
            <button
              onClick={onClearQueue}
              title="Clear Queue"
              className="p-1.5 text-text-2 hover:text-rose-400 hover:bg-bg-hover rounded transition-colors"
            >
              <Trash2 size={14} />
            </button>
          )}
          <button
            onClick={onClose}
            title="Close Drawer (Esc)"
            className="p-1.5 text-text-2 hover:text-text-1 hover:bg-bg-hover rounded transition-colors"
          >
            <X size={15} />
          </button>
        </div>
      </div>

      {/* Autoplay Radio Toggle */}
      <div className="flex-none px-4 py-2.5 border-b border-border-subtle flex items-center justify-between bg-bg-base/40 text-xs">
        <div className="flex items-center gap-2">
          <Radio size={14} className={autoplayRadio ? 'text-accent' : 'text-text-2'} />
          <span className="text-text-2 text-[11px] font-medium">Autoplay Radio</span>
        </div>
        <button
          onClick={onToggleAutoplayRadio}
          className={`w-9 h-5 rounded-full p-0.5 transition-colors ${
            autoplayRadio ? 'bg-accent' : 'bg-bg-hover border border-border-subtle'
          }`}
        >
          <div
            className={`w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
              autoplayRadio ? 'translate-x-4' : 'translate-x-0'
            }`}
          />
        </button>
      </div>

      {/* Now Playing Card */}
      {currentTrack && (
        <div className="flex-none p-3 border-b border-border-subtle bg-bg-hover/30">
          <div className="text-[10px] font-bold uppercase tracking-wider text-accent mb-2">
            Now Playing
          </div>
          <div className="flex items-center gap-2.5">
            <div className="w-11 h-11 rounded bg-bg-base border border-border-subtle overflow-hidden flex-none flex items-center justify-center">
              {currentTrack.thumbnail_url ? (
                <img
                  src={formatThumb(currentTrack.thumbnail_url)}
                  alt=""
                  className="w-full h-full object-cover"
                />
              ) : (
                <Music size={16} className="text-text-2" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-semibold text-text-1 truncate">
                {currentTrack.title}
              </div>
              <div className="text-[11px] text-text-2 truncate">{currentTrack.artist}</div>
            </div>
          </div>
        </div>
      )}

      {/* Up Next List */}
      <div className="flex-1 overflow-y-auto divide-y divide-white/[0.03]">
        <div className="px-4 py-2 text-[11px] font-semibold text-text-2 uppercase tracking-wider bg-bg-base/20 sticky top-0 z-10 backdrop-none">
          Up Next
        </div>

        {items.length === 0 ? (
          <div className="p-6 text-center text-xs text-text-2/70 italic">
            Queue is empty. Play a song or search to start playback!
          </div>
        ) : (
          items.map((item, index) => {
            const isPlayingThis = index === currentIndex;
            return (
              <div
                key={`${item.video_id}-${index}`}
                className={`flex items-center px-3 py-2 gap-2.5 hover:bg-bg-hover group transition-colors ${
                  isPlayingThis ? 'bg-accent/10 text-accent' : 'text-text-1'
                }`}
              >
                {/* Index / Hover play */}
                <div className="w-5 flex-none text-center text-[11px] text-text-2">
                  <span className="group-hover:hidden">{index + 1}</span>
                  <button
                    onClick={() => onPlayQueueItem(item, index)}
                    className="hidden group-hover:inline-block text-text-1 hover:text-accent"
                  >
                    <Play size={12} className="fill-current" />
                  </button>
                </div>

                {/* Cover tile */}
                <div className="w-8 h-8 rounded bg-bg-base border border-border-subtle overflow-hidden flex-none flex items-center justify-center">
                  {item.thumbnail_url ? (
                    <img
                      src={formatThumb(item.thumbnail_url)}
                      alt=""
                      loading="lazy"
                      decoding="async"
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <Music size={14} className="text-text-2" />
                  )}
                </div>

                {/* Title & Artist */}
                <div className="flex-1 min-w-0">
                  <div
                    className={`text-xs truncate ${
                      isPlayingThis ? 'text-accent font-semibold' : 'text-text-1'
                    }`}
                  >
                    {item.title}
                  </div>
                  <div className="text-[10px] text-text-2 truncate">{item.artist}</div>
                </div>

                {/* Move Up / Move Down buttons */}
                <div className="hidden group-hover:flex items-center gap-0.5 flex-none">
                  <button
                    onClick={() => index > 0 && onMoveQueueItem(index, index - 1)}
                    disabled={index === 0}
                    title="Move Up"
                    className="p-1 text-text-2 hover:text-text-1 disabled:opacity-20"
                  >
                    <ArrowUp size={12} />
                  </button>
                  <button
                    onClick={() =>
                      index < items.length - 1 && onMoveQueueItem(index, index + 1)
                    }
                    disabled={index === items.length - 1}
                    title="Move Down"
                    className="p-1 text-text-2 hover:text-text-1 disabled:opacity-20"
                  >
                    <ArrowDown size={12} />
                  </button>
                  <button
                    onClick={() => onRemoveQueueItem(index)}
                    title="Remove from queue"
                    className="p-1 text-text-2 hover:text-rose-400"
                  >
                    <X size={12} />
                  </button>
                </div>

                {/* Duration */}
                <div className="text-[11px] font-mono text-text-2 group-hover:hidden flex-none">
                  {formatTime(item.duration_s)}
                </div>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
};
