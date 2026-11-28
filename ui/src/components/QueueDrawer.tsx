import { memo, useState, useRef, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  X,
  Trash2,
  Radio,
  GripVertical,
  Play,
  Plus,
  Shuffle,
  Repeat,
  Repeat1,
} from 'lucide-react';
import { TrackImage } from './TrackImage';
import {
  TrackContextMenu,
  isTrackDownloaded,
  useTrackContextMenu,
} from './TrackContextMenu';
import { cancelTrackPreload, scheduleTrackPreload } from '../api/client';
import { useTwoFingerClose } from '../utils/gestures';
import type { DownloadProgress, QueueData, Track } from '../api/types';

interface QueueDrawerProps {
  isOpen: boolean;
  queue: QueueData | null;
  downloads?: Record<string, DownloadProgress>;
  downloadedIds?: Set<string>;
  onClose: () => void;
  onPlayQueueItem: (track: Track, index: number) => void;
  onRemoveQueueItem: (index: number) => void;
  onMoveQueueItem: (fromIdx: number, toIdx: number, targetSource?: 'user' | 'radio') => void;
  onPromoteQueueItem?: (index: number) => void;
  onClearQueue: () => void;
  onToggleAutoplayRadio: () => void;
  onToggleShuffle?: () => void;
  onCycleRepeat?: () => void;
  onAddNext?: (track: Track) => void;
  onAppendQueue?: (track: Track) => void;
  onOpenArtist?: (artistName: string, artistId?: string | null) => void;
  onOpenAlbum?: (albumName: string, albumId: string | null | undefined, track: Track) => void;
  onOpenAddToPlaylist?: (track: Track) => void;
  onDownload?: (track: Track) => void;
  onDeleteDownload?: (track: Track) => void;
}

interface FloatingGhost {
  track: Track;
  left: number;
  width: number;
  y: number;
}

type QueueFlatRow =
  | { type: 'header'; id: string; label: string; count: number; isRadio: boolean }
  | { type: 'item'; track: Track; origIndex: number; isRadio: boolean };

const HEADER_HEIGHT = 30;
const ROW_HEIGHT = 54;

function getQueueRowHeight(row: QueueFlatRow): number {
  return row.type === 'header' ? HEADER_HEIGHT : ROW_HEIGHT;
}

function findGlobalDropRowIndex(rows: QueueFlatRow[], contentY: number): number | null {
  let accY = 0;
  let bestIdx: number | null = null;
  let bestDist = Infinity;
  for (let i = 0; i < rows.length; i++) {
    const h = getQueueRowHeight(rows[i]);
    if (rows[i].type === 'item') {
      const dist = Math.abs(contentY - (accY + h / 2));
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }
    accY += h;
  }
  return bestIdx;
}

function computePreviewOffsetMap(
  rows: QueueFlatRow[],
  dragVisIdx: number | null,
  overVisIdx: number | null
): Map<number, number> | null {
  if (dragVisIdx === null || overVisIdx === null || dragVisIdx === overVisIdx) {
    return null;
  }
  const indexed = rows.map((row, idx) => ({ row, idx }));
  const origY = new Map<number, number>();
  let y0 = 0;
  for (const entry of indexed) {
    origY.set(entry.idx, y0);
    y0 += getQueueRowHeight(entry.row);
  }
  const moved = indexed.splice(dragVisIdx, 1)[0];
  if (!moved) return null;
  indexed.splice(overVisIdx, 0, moved);
  const shiftMap = new Map<number, number>();
  let y1 = 0;
  for (const entry of indexed) {
    shiftMap.set(entry.idx, y1 - (origY.get(entry.idx) ?? 0));
    y1 += getQueueRowHeight(entry.row);
  }
  return shiftMap;
}

export const QueueDrawer = memo(({
  isOpen,
  queue,
  downloads = {},
  downloadedIds,
  onClose,
  onPlayQueueItem,
  onRemoveQueueItem,
  onMoveQueueItem,
  onPromoteQueueItem,
  onClearQueue,
  onToggleAutoplayRadio,
  onToggleShuffle,
  onCycleRepeat,
  onAddNext,
  onAppendQueue,
  onOpenArtist,
  onOpenAlbum,
  onOpenAddToPlaylist,
  onDownload,
  onDeleteDownload,
}: QueueDrawerProps) => {
  const [rendered, setRendered] = useState<boolean>(isOpen);
  const [visible, setVisible] = useState<boolean>(false);
  const [dragVisIdx, setDragVisIdx] = useState<number | null>(null);
  const [overVisIdx, setOverVisIdx] = useState<number | null>(null);
  const [ghost, setGhost] = useState<FloatingGhost | null>(null);
  const { menuState, menuRef, openMenuAt, closeMenu } = useTrackContextMenu();

  useEffect(() => {
    if (isOpen) {
      setRendered(true);
      const raf1 = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(raf1);
    } else {
      setVisible(false);
      setRendered(false);
    }
  }, [isOpen]);

  const asideRef = useRef<HTMLElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const dragVisRef = useRef<number | null>(null);

  useTwoFingerClose({
    enabled: isOpen && rendered,
    ready: visible,
    settleMs: 420,
    containerRef: asideRef,
    onClose,
  });
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
  const repeatMode = queue?.repeat_mode || 'off';

  // Split upcoming items cleanly into Up Next (User Queue) & Suggested (Radio)
  const { upNextEntries, flatRows } = useMemo(() => {
    if (!items.length) return { upNextEntries: [] as { track: Track; origIndex: number }[], flatRows: [] as QueueFlatRow[] };
    const filtered = !currentTrack
      ? items.map((track, origIndex) => ({ track, origIndex }))
      : [
          ...items
            .slice(currentIndex + 1)
            .map((track, offset) => ({ track, origIndex: currentIndex + 1 + offset })),
          ...(repeatMode === 'all'
            ? items.slice(0, currentIndex).map((track, origIndex) => ({ track, origIndex }))
            : []),
        ];
    const userItems = filtered.filter(({ track }) => track.source !== 'radio');
    const radioItems = filtered.filter(({ track }) => track.source === 'radio');
    const rows: QueueFlatRow[] = [];
    if (userItems.length > 0) {
      rows.push({
        type: 'header',
        id: 'hdr-user',
        label: 'Up Next (Queue)',
        count: userItems.length,
        isRadio: false,
      });
      for (const u of userItems) {
        rows.push({ type: 'item', track: u.track, origIndex: u.origIndex, isRadio: false });
      }
    }
    if (radioItems.length > 0) {
      rows.push({
        type: 'header',
        id: 'hdr-radio',
        label: 'Suggested After Queue',
        count: radioItems.length,
        isRadio: true,
      });
      for (const r of radioItems) {
        rows.push({ type: 'item', track: r.track, origIndex: r.origIndex, isRadio: true });
      }
    }
    return { upNextEntries: filtered, flatRows: rows };
  }, [items, currentIndex, currentTrack, repeatMode]);

  const rowVirtualizer = useVirtualizer({
    count: rendered ? flatRows.length : 0,
    getScrollElement: () => listRef.current,
    estimateSize: (i) => (flatRows[i]?.type === 'header' ? HEADER_HEIGHT : ROW_HEIGHT),
    overscan: 6,
  });

  useEffect(() => {
    if (rendered) {
      rowVirtualizer.measure();
    }
  }, [rendered, rowVirtualizer, flatRows]);

  const previewShiftMap = useMemo(
    () => computePreviewOffsetMap(flatRows, dragVisIdx, overVisIdx),
    [flatRows, dragVisIdx, overVisIdx]
  );

  if (!rendered) return null;

  const formatTime = (secs?: number): string => {
    if (!secs || isNaN(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const recalcOverIndex = (clientY: number, rowsSnapshot: QueueFlatRow[]) => {
    const listEl = listRef.current;
    if (!listEl || rowsSnapshot.length <= 0) return;
    const rect = listEl.getBoundingClientRect();
    const contentY = clientY - rect.top + listEl.scrollTop;
    const idx = findGlobalDropRowIndex(rowsSnapshot, contentY);
    if (idx !== null && idx !== overVisRef.current) {
      overVisRef.current = idx;
      setOverVisIdx(idx);
    }
  };

  const handleRowPointerDown = (
    e: React.PointerEvent,
    visIdx: number,
    track: Track
  ) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('button')) return;

    dragCleanupRef.current?.();

    const listEl = listRef.current;
    const listRect = listEl?.getBoundingClientRect();
    const startY = e.clientY;
    const rowLeft = listRect ? listRect.left : 0;
    const rowWidth = listRect ? listRect.width : 320;
    const rowsSnapshot = flatRows;

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
          recalcOverIndex(py, rowsSnapshot);
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

      recalcOverIndex(moveEvent.clientY, rowsSnapshot);
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

      const fromRow = fromV !== null ? rowsSnapshot[fromV] : undefined;
      const toRow = toV !== null ? rowsSnapshot[toV] : undefined;
      if (
        wasDrag &&
        fromRow?.type === 'item' &&
        toRow?.type === 'item' &&
        (fromRow.origIndex !== toRow.origIndex || fromRow.isRadio !== toRow.isRadio)
      ) {
        onMoveQueueItem(fromRow.origIndex, toRow.origIndex, toRow.isRadio ? 'radio' : 'user');
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
    if (!previewShiftMap || visIdx === dragVisIdx) return 0;
    return previewShiftMap.get(visIdx) ?? 0;
  };

  return (
    <>
      <aside
        ref={asideRef}
        data-testid="queue-drawer"
        style={{
          transform: visible ? 'translate3d(0, 0, 0)' : 'translate3d(24px, 0, 0)',
          opacity: visible ? 1 : 0,
          transition: 'transform 220ms cubic-bezier(0.22, 1, 0.36, 1), opacity 180ms ease',
        }}
        className="orbit-drawer z-30 ios-liquid-drawer rounded-[24px] flex flex-col select-none overflow-hidden"
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

        <div ref={listRef} className="flex-1 overflow-y-auto relative" style={{ contain: 'strict' }}>
          {flatRows.length === 0 ? (
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
                const row = flatRows[visIdx];
                if (!row) return null;

                if (row.type === 'header') {
                  const hdrShift = getRowShiftY(visIdx);
                  return (
                    <div
                      key={row.id}
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: '100%',
                        height: `${HEADER_HEIGHT}px`,
                        transform: `translate3d(0, ${virtualRow.start + hdrShift}px, 0)`,
                        transition:
                          dragVisIdx !== null
                            ? 'transform 180ms cubic-bezier(0.22, 1, 0.36, 1)'
                            : undefined,
                      }}
                      className="z-10 flex items-center justify-between px-4 bg-[#0B0F1A]/95 border-y border-white/[0.08] text-[10px] font-semibold uppercase tracking-[0.16em] text-[#9AA5C4]"
                    >
                      <span>{row.label}</span>
                      <span className="font-mono tabular-nums text-[9.5px] text-[#7A84A6]">
                        {!row.isRadio && upNextEntries.length > 1
                          ? `Drag to reorder · ${row.count}`
                          : row.count}
                      </span>
                    </div>
                  );
                }

                const { track: item, origIndex, isRadio } = row;
                const isDragged = dragVisIdx === visIdx;
                const shiftY = getRowShiftY(visIdx);

                return (
                  <div
                    key={`${item.video_id}-${origIndex}`}
                    onPointerDown={(e) => handleRowPointerDown(e, visIdx, item)}
                    onMouseEnter={() => scheduleTrackPreload(item)}
                    onMouseLeave={() => cancelTrackPreload(item.video_id)}
                    onContextMenu={(e) => {
                      if (!onAddNext) return;
                      e.preventDefault();
                      e.stopPropagation();
                      openMenuAt(
                        item,
                        e.clientX,
                        e.clientY,
                        isTrackDownloaded(item, downloadedIds, downloads)
                      );
                    }}
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
                    } ${isRadio ? 'opacity-85 hover:opacity-100' : ''}`}
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

                    {isRadio && onPromoteQueueItem && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onPromoteQueueItem(origIndex);
                        }}
                        title="Move into Active Queue"
                        className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-full bg-white/[0.06] hover:bg-white/[0.16] text-[10px] font-medium text-[#D4DCFA] hover:text-white flex-none transition-colors"
                      >
                        <Plus size={11} />
                        <span>Queue</span>
                      </button>
                    )}

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

        {(onToggleShuffle || onCycleRepeat || items.length > 0) && (
          <div className="flex-none px-3 py-2 border-t border-white/[0.08] bg-white/[0.025] flex items-center justify-between gap-2 text-[11px]">
            <div className="flex items-center gap-1.5">
              {onToggleShuffle && (
                <button
                  type="button"
                  onClick={onToggleShuffle}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border transition-colors ${
                    queue?.shuffle
                      ? 'bg-white/[0.12] border-white/20 text-white'
                      : 'border-white/[0.08] text-[#9AA5C4] hover:text-white hover:bg-white/[0.06]'
                  }`}
                  style={queue?.shuffle ? { color: 'var(--dynamic-accent)' } : undefined}
                >
                  <Shuffle size={12} />
                  <span>Shuffle</span>
                </button>
              )}
              {onCycleRepeat && (
                <button
                  type="button"
                  onClick={onCycleRepeat}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border transition-colors ${
                    repeatMode !== 'off'
                      ? 'bg-white/[0.12] border-white/20 text-white'
                      : 'border-white/[0.08] text-[#9AA5C4] hover:text-white hover:bg-white/[0.06]'
                  }`}
                  style={repeatMode !== 'off' ? { color: 'var(--dynamic-accent)' } : undefined}
                >
                  {repeatMode === 'one' ? <Repeat1 size={12} /> : <Repeat size={12} />}
                  <span className="capitalize">{repeatMode === 'off' ? 'Repeat' : repeatMode}</span>
                </button>
              )}
            </div>

            {items.length > 0 && (
              <button
                type="button"
                onClick={onClearQueue}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[#9AA5C4] hover:text-[#FF6B9E] hover:bg-[#FF4D8D]/10 transition-colors"
              >
                <Trash2 size={11} />
                <span>Clear</span>
              </button>
            )}
          </div>
        )}
      </aside>

      {onAddNext && (
        <TrackContextMenu
          menuState={menuState}
          menuRef={menuRef}
          onClose={closeMenu}
          onAddNext={onAddNext}
          onAppendQueue={onAppendQueue}
          onOpenArtist={onOpenArtist}
          onOpenAlbum={onOpenAlbum}
          onOpenAddToPlaylist={onOpenAddToPlaylist}
          onDownload={onDownload}
          onDeleteDownload={onDeleteDownload}
        />
      )}

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
