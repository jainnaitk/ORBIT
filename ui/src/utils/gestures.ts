import { useEffect, useRef, type RefObject } from 'react';

interface TwoFingerCloseOptions {
  enabled: boolean;
  /**
   * Stabilization gate: set to `false` while results/data are loading or settling.
   * Gesture remains locked until `ready` has been `true` continuously for `settleMs`.
   */
  ready?: boolean;
  /** Minimum ms after `ready` becomes true before a close gesture is accepted (default 450ms). */
  settleMs?: number;
  containerRef: RefObject<HTMLElement | null>;
  onClose: () => void;
}

function findScrollableAncestor(
  target: EventTarget | null,
  boundary: HTMLElement
): HTMLElement | null {
  let node = target instanceof HTMLElement ? target : null;
  while (node && node !== boundary) {
    const style = window.getComputedStyle(node);
    const overflowY = style.overflowY;
    if (
      (overflowY === 'auto' || overflowY === 'scroll') &&
      node.scrollHeight > node.clientHeight + 4
    ) {
      return node;
    }
    node = node.parentElement;
  }
  return null;
}

/**
 * Stabilized, intentional two-finger swipe-down close gesture for both:
 * - Touchscreens: 2 active fingers moving firmly downward (>= 115px within 420ms)
 * - Windows Precision Touchpads: deliberate, hard, high-velocity vertical pull
 *   AFTER content has stabilized and AFTER any list scrolling has come to a full rest
 *   at scrollTop === 0 for >= 420ms (prevents scroll-to-top momentum from ever closing).
 */
export function useTwoFingerClose({
  enabled,
  ready = true,
  settleMs = 450,
  containerRef,
  onClose,
}: TwoFingerCloseOptions): void {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const readySinceRef = useRef<number>(ready ? performance.now() : 0);

  useEffect(() => {
    if (ready) {
      if (readySinceRef.current === 0) {
        readySinceRef.current = performance.now();
      }
    } else {
      readySinceRef.current = 0;
    }
  }, [ready]);

  useEffect(() => {
    if (!enabled) return;
    const el = containerRef.current;
    if (!el) return;

    // Reset stabilization timer on mount/enable
    readySinceRef.current = ready ? performance.now() : 0;

    let lastScrollActivityAt = 0;
    const SCROLL_REST_GUARD_MS = 420;

    const onAnyScroll = () => {
      lastScrollActivityAt = performance.now();
    };

    const isStabilized = (now: number): boolean => {
      if (readySinceRef.current === 0) return false;
      if (now - readySinceRef.current < settleMs) return false;
      return true;
    };

    // --- Two-finger touchscreen detector ---
    let touchStartY = 0;
    let touchStartX = 0;
    let touchStartAt = 0;
    let trackingTwoFinger = false;

    const onTouchStart = (e: TouchEvent) => {
      const now = performance.now();
      if (e.touches.length === 2 && isStabilized(now)) {
        const scrollable = findScrollableAncestor(e.target, el);
        if (scrollable && (scrollable.scrollTop > 1 || now - lastScrollActivityAt < SCROLL_REST_GUARD_MS)) {
          trackingTwoFinger = false;
          return;
        }
        trackingTwoFinger = true;
        touchStartAt = now;
        touchStartY = (e.touches[0].clientY + e.touches[1].clientY) * 0.5;
        touchStartX = (e.touches[0].clientX + e.touches[1].clientX) * 0.5;
      } else {
        trackingTwoFinger = false;
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      if (!trackingTwoFinger || e.touches.length !== 2) return;
      const now = performance.now();
      if (now - touchStartAt > 420) {
        trackingTwoFinger = false;
        return;
      }
      const curY = (e.touches[0].clientY + e.touches[1].clientY) * 0.5;
      const curX = (e.touches[0].clientX + e.touches[1].clientX) * 0.5;
      const dy = curY - touchStartY;
      const dx = Math.abs(curX - touchStartX);
      if (dy >= 115 && dy > dx * 1.7) {
        trackingTwoFinger = false;
        onCloseRef.current();
      }
    };

    const onTouchEnd = (e: TouchEvent) => {
      if (e.touches.length < 2) {
        trackingTwoFinger = false;
      }
    };

    // --- Windows Precision Touchpad deliberate hard-swipe detector ---
    let accY = 0;
    let accX = 0;
    let peakStepY = 0;
    let wheelEvents = 0;
    let windowStart = 0;
    let lastEventAt = 0;
    let lastDir = 0;

    const resetWheel = () => {
      accY = 0;
      accX = 0;
      peakStepY = 0;
      wheelEvents = 0;
      windowStart = 0;
      lastEventAt = 0;
      lastDir = 0;
    };

    const onWheel = (e: WheelEvent) => {
      // Ignore pinch-to-zoom, line-based mouse wheel clicks, or tiny noise
      if (e.ctrlKey || e.deltaMode !== 0 || Math.abs(e.deltaY) < 2) {
        resetWheel();
        return;
      }

      const now = performance.now();
      if (!isStabilized(now)) {
        resetWheel();
        return;
      }

      const scrollable = findScrollableAncestor(e.target, el);
      if (scrollable) {
        // If list is not at the very top, or was just scrolling (scroll-to-top momentum), NEVER trigger
        if (scrollable.scrollTop > 1) {
          lastScrollActivityAt = now;
          resetWheel();
          return;
        }
        if (now - lastScrollActivityAt < SCROLL_REST_GUARD_MS) {
          resetWheel();
          return;
        }
        // At scrollTop === 0 inside a scrollable list, deltaY > 0 scrolls down into the list;
        // only overscroll pull-down (deltaY < 0) is a non-scrolling close intent.
        if (e.deltaY >= 0) {
          resetWheel();
          return;
        }
      } else if (now - lastScrollActivityAt < 220) {
        resetWheel();
        return;
      }

      // If there was a pause between gestures (> 130ms), start a fresh gesture window
      const dir = e.deltaY > 0 ? 1 : -1;
      if (
        windowStart === 0 ||
        now - lastEventAt > 130 ||
        now - windowStart > 320 ||
        (lastDir !== 0 && dir !== lastDir)
      ) {
        windowStart = now;
        accY = 0;
        accX = 0;
        peakStepY = 0;
        wheelEvents = 0;
        lastDir = dir;
      }

      lastEventAt = now;
      accY += e.deltaY;
      accX += e.deltaX;
      peakStepY = Math.max(peakStepY, Math.abs(e.deltaY));
      wheelEvents += 1;

      const absY = Math.abs(accY);
      const absX = Math.abs(accX);

      // Deliberate, hard, fast gesture threshold:
      // >= 5 smooth touchpad ticks, >= 260px cumulative travel in <= 320ms,
      // peak velocity >= 22px/tick, and strongly vertical (2x vertical over horizontal)
      if (wheelEvents >= 5 && absY >= 260 && peakStepY >= 22 && absY > absX * 2.0) {
        resetWheel();
        onCloseRef.current();
      }
    };

    el.addEventListener('scroll', onAnyScroll, { capture: true, passive: true });
    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchmove', onTouchMove, { passive: true });
    el.addEventListener('touchend', onTouchEnd, { passive: true });
    el.addEventListener('wheel', onWheel, { passive: true });

    return () => {
      el.removeEventListener('scroll', onAnyScroll, { capture: true });
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchmove', onTouchMove);
      el.removeEventListener('touchend', onTouchEnd);
      el.removeEventListener('wheel', onWheel);
    };
  }, [enabled, ready, settleMs, containerRef]);
}
