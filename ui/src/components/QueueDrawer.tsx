import { memo, useState, useRef, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useVirtualizer } from '@tanstack/react-virtual';
import { X, Trash2, Radio, GripVertical, Play } from 'lucide-react';
import { TrackImage } from './TrackImage';
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

interface FloatingGhost {
  track: Track;
  left: number;
  width: number;
  y: number;
}

const ROW_HEIGHT = 54;

export const QueueDrawer = memo(({
  isOpen,
  queue,
  onClose,
  onPlayQueueItem,
  onRemoveQueueItem,
  onMoveQueueItem,
  onClearQueue,
  onToggleAutoplayRadio,
}: QueueDrawerProps) => {
  const [rendered, setRendered] = useState<boolean>(isOpen);
  const [visible, setVisible] = useState<boolean>(false);
  const [dragVisIdx, setDragVisIdx] = useState<number | null>(null);
  const [overVisIdx, setOverVisIdx] = useState<number | null>(null);
  const [ghost, setGhost] = useState<FloatingGhost | null>(null);

  useEffect(() => {
    if (isOpen) {
      setRendered(true);
      let raf2 = 0;
      const raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => setVisible(true));
      });
      return () => {
        cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);
      };
    } else {
      setVisible(false);
      const t = window.setTimeout(() => setRendered(false), 210);
      return () => clearTimeout(t);
    }
  }, [isOpen]);

  const listRef = useRef<HTMLDivElement>(null);
  const dragVisRef = useRef<number | null>(null);
  const overVisRef = useRef<number | null>(null);
  const didDragRef = useRef<boolean>(false);
  const pointerYRef = useRef<number>(0);
  const autoScrollRafRef = useRef<number | null>(null);
  const dragCleanupRef = useRef<(() => void) | null>(null);
  const resetDragTimerRef = useRef<number | null>(null);
  const mountedRef = useRef<boolean>(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      dragCleanupRef.current?.();
      dragCleanupRef.current = null;
      if (autoScrollRafRef.current !== null) {
        cancelAnimationFrame(autoScrollRafRef.current);
        autoScrollRafRef.current = null;
      }
      if (resetDragTimerRef.current !== null) {
        clearTimeout(resetDragTimerRef.current);
        resetDragTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!isOpen) {
      dragCleanupRef.current?.();
      dragCleanupRef.current = null;
      setDragVisIdx(null);
      setOverVisIdx(null);
      setGhost(null);
    }
  }, [isOpen]);

  const items = queue?.items || [];
  const currentTrack = queue?.current_track || null;
  const currentIndex = queue?.current_index ?? 0;
  const autoplayRadio = queue?.autoplay_radio ?? true;

  // Exclude the currently playing song from UP NEXT so it never duplicates NOW ORBITING
  const upNextEntries = useMemo(() => {
    if (!items.length) return [];
    return items
      .map((track, origIndex) => ({ track, origIndex }))
      .filter(({ origIndex }) => !(currentTrack && origIndex === currentIndex));
  }, [items, currentIndex, currentTrack]);

  const rowVirtualizer = useVirtualizer({
    count: rendered ? upNextEntries.length : 0,
    getScrollElement: () => listRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 3,
  });

  if (!rendered) return null;

  const formatTime = (secs?: number): string => {
    if (!secs || isNaN(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const recalcOverIndex = (clientY: number, itemCount: number) => {
    const listEl = listRef.current;
    if (!listEl || itemCount <= 0) return;
    const rect = listEl.getBoundingClientRect();
    const contentY = clientY - rect.top + listEl.scrollTop;
    const idx = Math.max(0, Math.min(itemCount - 1, Math.floor(contentY / ROW_HEIGHT)));
    if (idx !== overVisRef.current) {
      overVisRef.current = idx;
      setOverVisIdx(idx);
    }
  };

  const handleRowPointerDown = (e: React.PointerEvent, visIdx: number, track: Track) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('button')) return;

    dragCleanupRef.current?.();

    const listEl = listRef.current;
    const listRect = listEl?.getBoundingClientRect();
    const startY = e.clientY;
    const rowLeft = listRect ? listRect.left : 0;
    const rowWidth = listRect ? listRect.width : 320;
    const entriesSnapshot = upNextEntries;
    const totalCount = entriesSnapshot.length;

    dragVisRef.current = visIdx;
    overVisRef.current = visIdx;
    didDragRef.current = false;
    pointerYRef.current = e.clientY;

    const cleanupListeners = () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerUp);
      if (autoScrollRafRef.current !== null) {
        cancelAnimationFrame(autoScrollRafRef.current);
        autoScrollRafRef.current = null;
      }
      if (dragCleanupRef.current === cleanupListeners) {
        dragCleanupRef.current = null;
      }
    };

    const runAutoScroll = () => {
      if (!mountedRef.current || !didDragRef.current || !listRef.current) {
        autoScrollRafRef.current = null;
        return;
      }
      const el = listRef.current;
      const rect = el.getBoundingClientRect();
      const py = pointerYRef.current;
      const edgeZone = 54;
      let delta = 0;

      if (py < rect.top + edgeZone) {
        const ratio = Math.min(1.6, (rect.top + edgeZone - py) / edgeZone);
        delta = -Math.max(3, ratio * 12);
      } else if (py > rect.bottom - edgeZone) {
        const ratio = Math.min(1.6, (py - (rect.bottom - edgeZone)) / edgeZone);
        delta = Math.max(3, ratio * 12);
      }

      if (delta !== 0) {
        const prevTop = el.scrollTop;
        el.scrollTop += delta;
        if (el.scrollTop !== prevTop) {
          recalcOverIndex(py, totalCount);
        }
      }
      autoScrollRafRef.current = requestAnimationFrame(runAutoScroll);
    };

    const onPointerMove = (moveEvent: PointerEvent) => {
      if (!mountedRef.current) {
        cleanupListeners();
        return;
      }
      pointerYRef.current = moveEvent.clientY;
      if (!didDragRef.current && Math.abs(moveEvent.clientY - startY) < 5) return;
      if (!didDragRef.current) {
        didDragRef.current = true;
        setDragVisIdx(visIdx);
        if (autoScrollRafRef.current === null) {
          autoScrollRafRef.current = requestAnimationFrame(runAutoScroll);
        }
      }

      setGhost({
        track,
        left: rowLeft,
        width: rowWidth,
        y: moveEvent.clientY - ROW_HEIGHT * 0.5,
      });

      recalcOverIndex(moveEvent.clientY, totalCount);
    };

    const onPointerUp = () => {
      cleanupListeners();

      const fromV = dragVisRef.current;
      const toV = overVisRef.current;
      const wasDrag = didDragRef.current;

      dragVisRef.current = null;
      overVisRef.current = null;

      if (!mountedRef.current) return;

      setDragVisIdx(null);
      setOverVisIdx(null);
      setGhost(null);

      if (wasDrag && fromV !== null && toV !== null && fromV !== toV) {
        const fromOrig = entriesSnapshot[fromV]?.origIndex;
        const toOrig = entriesSnapshot[toV]?.origIndex;
        if (fromOrig !== undefined && toOrig !== undefined && fromOrig !== toOrig) {
          onMoveQueueItem(fromOrig, toOrig);
        }
      }
      if (wasDrag) {
        if (resetDragTimerRef.current !== null) clearTimeout(resetDragTimerRef.current);
        resetDragTimerRef.current = window.setTimeout(() => {
          didDragRef.current = false;
          resetDragTimerRef.current = null;
        }, 60);
      }
    };

    dragCleanupRef.current = cleanupListeners;
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);
  };

  const getRowShiftY = (visIdx: number): number => {
    if (dragVisIdx === null || overVisIdx === null || visIdx === dragVisIdx) return 0;
    if (dragVisIdx < overVisIdx && visIdx > dragVisIdx && visIdx <= overVisIdx) return -ROW_HEIGHT;
    if (dragVisIdx > overVisIdx && visIdx >= overVisIdx && visIdx < dragVisIdx) return ROW_HEIGHT;
    return 0;
  };

  return (
    <>
      <aside
        style={{
          transform: visible ? 'translate3d(0, 0, 0)' : 'translate3d(30px, 0, 0)',
          opacity: visible ? 1 : 0,
          transition: 'transform 280ms cubic-bezier(0.22, 1, 0.36, 1), opacity 220ms ease',
          willChange: 'transform, opacity',
        }}
        className="absolute top-1 right-0 bottom-1 w-[min(326px,100%)] ios-liquid-drawer rounded-[24px] flex flex-col z-40 select-none overflow-hidden"
      >
        <div className="h-12 flex-none px-4 flex items-center justify-between border-b border-white/[0.10]">
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold uppercase tracking-[0.18em] text-[#F4F6FF]">
              Queue
            </span>
            <span className="text-[10px] font-mono tabular-nums text-[#A6B0CF] bg-white/[0.08] px-2 py-0.5 rounded-full">
              {upNextEntries.length}
            </span>
          </div>

          <div className="flex items-center gap-1">
            {items.length > 0 && (
              <button
                onClick={onClearQueue}
                title="Clear Queue"
                className="p-1.5 text-[#A6B0CF] hover:text-[#FF4D8D] hover:bg-white/[0.10] rounded-full transition-colors"
              >
                <Trash2 size={14} />
              </button>
            )}
            <button
              onClick={onClose}
              title="Close Queue (Esc)"
              className="p-1.5 text-[#A6B0CF] hover:text-white hover:bg-white/[0.10] rounded-full transition-colors"
            >
              <X size={15} />
            </button>
          </div>
        </div>

        <div className="flex-none px-4 py-2.5 border-b border-white/[0.08] flex items-center justify-between text-xs bg-white/[0.025]">
          <div className="flex items-center gap-2">
            <Radio
              size={14}
              style={autoplayRadio ? { color: 'var(--dynamic-accent)' } : { color: '#646E8F' }}
            />
            <span className="text-[#A6B0CF] text-[11px] font-medium">Autoplay Radio</span>
          </div>
          <button
            onClick={onToggleAutoplayRadio}
            className={`w-9 h-5 rounded-full p-0.5 transition-colors ${
              autoplayRadio ? '' : 'bg-white/10 border border-white/15'
            }`}
            style={autoplayRadio ? { background: 'var(--dynamic-accent)' } : undefined}
          >
            <div
              className={`w-4 h-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                autoplayRadio ? 'translate-x-4' : 'translate-x-0'
              }`}
            />
          </button>
        </div>

        {currentTrack && (
          <div className="flex-none p-3.5 border-b border-white/[0.09] bg-white/[0.04]">
            <div
              className="text-[10px] font-bold uppercase tracking-[0.18em] mb-2"
              style={{ color: 'var(--dynamic-accent)' }}
            >
              Now Orbiting
            </div>
            <div className="flex items-center gap-2.5">
              <div className="w-10 h-10 rounded-full bg-black border border-white/20 overflow-hidden flex-none">
                <TrackImage
                  src={currentTrack.thumbnail_url}
                  videoId={currentTrack.video_id}
                  size="sm"
                  iconSize={16}
                  eager
                />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold text-[#F4F6FF] truncate">
                  {currentTrack.title}
                </div>
                <div className="text-[11px] text-[#A6B0CF] truncate">{currentTrack.artist}</div>
              </div>
            </div>
          </div>
        )}

        <div className="flex-none px-4 py-2 text-[10px] font-semibold text-[#A6B0CF] uppercase tracking-[0.16em] bg-white/[0.03] border-b border-white/[0.08] flex items-center justify-between">
          <span>Up Next</span>
          {upNextEntries.length > 1 && (
            <span className="text-[9px] font-normal normal-case text-[#7A84A6]">
              Drag anywhere to reorder
            </span>
          )}
        </div>

        <div ref={listRef} className="flex-1 overflow-y-auto relative" style={{ contain: 'strict' }}>
          {upNextEntries.length === 0 ? (
            <div className="p-6 text-center text-xs text-[#7A84A6] italic">
              {currentTrack
                ? autoplayRadio
                  ? 'Autoplay Radio will queue similar tracks next.'
                  : 'No tracks queued up next.'
                : 'Queue is empty. Play a track to launch auto-radio.'}
            </div>
          ) : (
            <div
              style={{
                height: `${rowVirtualizer.getTotalSize()}px`,
                width: '100%',
                position: 'relative',
              }}
            >
              {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                const visIdx = virtualRow.index;
                const entry = upNextEntries[visIdx];
                if (!entry) return null;
                const { track: item, origIndex } = entry;
                const isDragged = dragVisIdx === visIdx;
                const shiftY = getRowShiftY(visIdx);

                return (
                  <div
                    key={`${item.video_id}-${origIndex}`}
                    onPointerDown={(e) => handleRowPointerDown(e, visIdx, item)}
                    onClick={() => {
                      if (didDragRef.current) return;
                      onPlayQueueItem(item, origIndex);
                    }}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      height: `${ROW_HEIGHT}px`,
                      transform: `translate3d(0, ${virtualRow.start + shiftY}px, 0)`,
                      transition:
                        dragVisIdx !== null
                          ? 'transform 180ms cubic-bezier(0.22, 1, 0.36, 1), opacity 140ms ease'
                          : 'background-color 150ms ease',
                    }}
                    className={`flex items-center px-3 gap-2.5 border-b border-white/[0.05] hover:bg-white/[0.08] group cursor-grab active:cursor-grabbing ${
                      isDragged ? 'opacity-15' : ''
                    }`}
                  >
                    <div className="w-4 flex-none flex items-center justify-center text-[#646E8F] group-hover:text-[#D4DCFA]">
                      <GripVertical size={13} />
                    </div>

                    <div className="relative w-8 h-8 rounded-lg bg-black border border-white/15 overflow-hidden flex-none">
                      <TrackImage
                        src={item.thumbnail_url}
                        videoId={item.video_id}
                        size="sm"
                        iconSize={14}
                      />
                      <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
                        <Play size={11} className="fill-white text-white ml-0.5" />
                      </div>
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-[#F4F6FF] truncate">{item.title}</div>
                      <div className="text-[10px] text-[#A6B0CF] truncate">{item.artist}</div>
                    </div>

                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onRemoveQueueItem(origIndex);
                      }}
                      title="Remove"
                      className="hidden group-hover:flex p-1 text-[#A6B0CF] hover:text-[#FF4D8D] rounded flex-none"
                    >
                      <X size={13} />
                    </button>

                    <div className="text-[11px] font-mono tabular-nums text-[#646E8F] group-hover:hidden flex-none">
                      {formatTime(item.duration_s)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </aside>

      {ghost &&
        createPortal(
          <div
            style={{
              position: 'fixed',
              left: `${ghost.left + 4}px`,
              top: 0,
              width: `${ghost.width - 8}px`,
              height: `${ROW_HEIGHT - 4}px`,
              transform: `translate3d(0, ${ghost.y}px, 0) scale(1.03)`,
              borderColor: 'rgba(var(--dynamic-accent-rgb), 0.70)',
            }}
            className="pointer-events-none z-[9999] ios-liquid-glass rounded-2xl px-3 flex items-center gap-2.5"
          >
            <GripVertical size={13} style={{ color: 'var(--dynamic-accent)' }} className="flex-none" />
            <div className="w-8 h-8 rounded-lg bg-black border border-white/20 overflow-hidden flex-none">
              <TrackImage
                src={ghost.track.thumbnail_url}
                videoId={ghost.track.video_id}
                size="sm"
                iconSize={14}
                eager
              />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-semibold text-white truncate">{ghost.track.title}</div>
              <div className="text-[10px] text-[#A6B0CF] truncate">{ghost.track.artist}</div>
            </div>
          </div>,
          document.body
        )}
    </>
  );
});
