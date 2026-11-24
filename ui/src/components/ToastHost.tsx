import { useEffect, useState, useCallback, useRef } from 'react';
import { CheckCircle2, Info, Sparkles, AlertCircle, X } from 'lucide-react';

export type ToastKind = 'info' | 'success' | 'accent' | 'error';

export interface ToastItem {
  id: number;
  message: string;
  kind: ToastKind;
  durationMs: number;
}

type ToastListener = (item: ToastItem) => void;

const listeners = new Set<ToastListener>();
let nextToastId = 1;

export const toast = {
  show(message: string, kind: ToastKind = 'info', durationMs: number = 2800): void {
    const trimmed = message.trim();
    if (!trimmed) return;
    const item: ToastItem = {
      id: nextToastId++,
      message: trimmed,
      kind,
      durationMs,
    };
    listeners.forEach((fn) => fn(item));
  },
};

const MAX_VISIBLE_TOASTS = 4;

export const ToastHost = () => {
  const [items, setItems] = useState<ToastItem[]>([]);
  const timersRef = useRef<Map<number, number>>(new Map());

  const dismiss = useCallback((id: number) => {
    const timerId = timersRef.current.get(id);
    if (timerId !== undefined) {
      clearTimeout(timerId);
      timersRef.current.delete(id);
    }
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  useEffect(() => {
    const timers = timersRef.current;

    const onToast: ToastListener = (item) => {
      setItems((prev) => {
        const next = [...prev, item];
        if (next.length > MAX_VISIBLE_TOASTS) {
          const dropped = next.shift();
          if (dropped) {
            const oldTimer = timers.get(dropped.id);
            if (oldTimer !== undefined) {
              clearTimeout(oldTimer);
              timers.delete(dropped.id);
            }
          }
        }
        return next;
      });

      const timerId = window.setTimeout(() => {
        timers.delete(item.id);
        setItems((prev) => prev.filter((t) => t.id !== item.id));
      }, item.durationMs);
      timers.set(item.id, timerId);
    };

    listeners.add(onToast);
    return () => {
      listeners.delete(onToast);
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    };
  }, []);

  if (items.length === 0) return null;

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="fixed bottom-[102px] right-4 z-[95] flex flex-col items-end gap-2 pointer-events-none max-w-[320px]"
    >
      {items.map((item) => {
        const borderStyle =
          item.kind === 'error'
            ? 'rgba(255, 77, 141, 0.45)'
            : item.kind === 'success'
            ? 'rgba(52, 211, 153, 0.42)'
            : item.kind === 'accent'
            ? 'rgba(var(--dynamic-accent-rgb), 0.48)'
            : 'rgba(255, 255, 255, 0.16)';

        return (
          <div
            key={item.id}
            role="status"
            style={{ borderColor: borderStyle }}
            className="pointer-events-auto ios-liquid-popover eh-popover-in rounded-2xl px-3.5 py-2.5 flex items-center gap-2.5 text-xs text-[#F1F4FA] shadow-[0_14px_34px_rgba(0,0,0,0.68)]"
          >
            {item.kind === 'success' ? (
              <CheckCircle2 size={14} className="text-[#34D399] flex-none" />
            ) : item.kind === 'error' ? (
              <AlertCircle size={14} className="text-[#FF4D8D] flex-none" />
            ) : item.kind === 'accent' ? (
              <Sparkles size={14} style={{ color: 'var(--dynamic-accent)' }} className="flex-none" />
            ) : (
              <Info size={14} className="text-[#9AA5C4] flex-none" />
            )}

            <span className="leading-snug truncate max-w-[230px]" title={item.message}>
              {item.message}
            </span>

            <button
              type="button"
              onClick={() => dismiss(item.id)}
              aria-label="Dismiss notification"
              className="p-0.5 text-[#8E99BA] hover:text-white rounded-full hover:bg-white/[0.08] transition-colors flex-none"
            >
              <X size={12} />
            </button>
          </div>
        );
      })}
    </div>
  );
};
