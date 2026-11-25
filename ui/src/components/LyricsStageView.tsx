import {
  useEffect,
  useLayoutEffect,
  useState,
  useRef,
  useCallback,
  useMemo,
} from 'react';
import {
  AlignLeft,
  ArrowDown,
  Copy,
  Languages,
  MoreVertical,
  RefreshCw,
  RotateCcw,
  SlidersHorizontal,
} from 'lucide-react';
import { bridgeApi, bridgeSocket } from '../api/client';
import { toast } from './ToastHost';
import { LiquidGlassLoader } from './LiquidGlassLoader';
import { detectLyricsLanguage, hasNonLatinScript, romanizeText } from '../utils/romanize';
import type {
  LyricLine,
  LyricVariant,
  LyricWord,
  LyricsSyncType,
  PlaybackTick,
  QueueData,
  Track,
} from '../api/types';

const OFFSET_STORAGE_KEY = 'orbit_lyrics_offset_v1';
const MAX_STORED_OFFSETS = 150;
const MIN_OFFSET_MS = -5000;
const MAX_OFFSET_MS = 5000;
const SYNC_LEAD_SEC = 0.04;
const MAX_INTERP_ELAPSED_SEC = 0.55;

function readStoredOffsets(): Record<string, number> {
  try {
    const raw = window.localStorage.getItem(OFFSET_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, number>) : {};
  } catch {
    return {};
  }
}

function loadOffsetMs(videoId: string | null): number {
  if (!videoId) return 0;
  const map = readStoredOffsets();
  const val = map[videoId];
  if (typeof val === 'number' && Number.isFinite(val)) {
    return Math.max(MIN_OFFSET_MS, Math.min(MAX_OFFSET_MS, Math.round(val)));
  }
  return 0;
}

function saveOffsetMs(videoId: string | null, offsetMs: number): void {
  if (!videoId) return;
  try {
    const map = readStoredOffsets();
    if (offsetMs === 0) {
      delete map[videoId];
    } else {
      map[videoId] = Math.max(MIN_OFFSET_MS, Math.min(MAX_OFFSET_MS, Math.round(offsetMs)));
    }
    const keys = Object.keys(map);
    if (keys.length > MAX_STORED_OFFSETS) {
      for (const oldKey of keys.slice(0, keys.length - MAX_STORED_OFFSETS)) {
        delete map[oldKey];
      }
    }
    window.localStorage.setItem(OFFSET_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Ignore storage quota errors
  }
}

function formatOffsetMs(ms: number): string {
  if (ms === 0) return '0 ms';
  return `${ms > 0 ? '+' : ''}${ms} ms`;
}

function romanizeSyncedLines(lines: LyricLine[] | null): LyricLine[] | null {
  if (!lines) return null;
  return lines.map((line) => {
    if (line.type === 'instrumental') return line;
    const officialRoman = line.romanized?.trim();
    let romanWords = line.words;
    if (line.words && line.words.length > 0) {
      const officialParts = officialRoman ? officialRoman.split(/\s+/) : null;
      if (officialParts && officialParts.length === line.words.length) {
        romanWords = line.words.map((w, idx) => ({
          ...w,
          text: officialParts[idx] + (/\s$/.test(w.text) ? ' ' : ''),
        }));
      } else {
        romanWords = line.words.map((w) => ({ ...w, text: romanizeText(w.text) }));
      }
    }
    return {
      ...line,
      text: (officialRoman || (line.text ? romanizeText(line.text) : ''))
        .replace(/,(?=\S)/g, ', ')
        .replace(/\s+,/g, ','),
      words: romanWords?.map((w, idx) => {
        let t = w.text.replace(/,(?=\S)/g, ', ');
        if (idx < romanWords!.length - 1 && t.endsWith(',')) {
          t += ' ';
        }
        return { ...w, text: t };
      }),
    };
  });
}

/**
 * Priority order for language pills:
 * Romanized (Latin default) -> English -> Hindi -> other original language.
 */
function variantPriority(code: string): number {
  const c = code.toLowerCase();
  if (c === 'roman') return 0;
  if (c === 'en') return 1;
  if (c === 'hi') return 2;
  return 3;
}

/**
 * Compute layout-stable vertical offset of `el` inside `container`, completely immune
 * to CSS `transform: scale()` transitions on the active lyric line (zero glide wobble).
 */
function getOffsetTopInContainer(el: HTMLElement, container: HTMLElement): number {
  let top = 0;
  let node: HTMLElement | null = el;
  while (node && node !== container) {
    top += node.offsetTop;
    node = node.offsetParent as HTMLElement | null;
  }
  return top;
}

/**
 * Duration-locked word-by-word glide fallback for tracks where NO provider has word/syllable
 * timestamps (only line-level timestamps). Unlike fast fixed-rate pacing, this spans the
 * ACTUAL line duration (`line.end - line.time` or `nextLine.time - line.time`) weighted by
 * syllable count + punctuation pauses so the highlight glides at the true pace of the line.
 */
function buildDurationLockedWords(line: LyricLine, nextLine?: LyricLine): LyricWord[] | null {
  if (line.type === 'instrumental') return null;
  const tokens = (line.text || '').trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return null;

  const hasExplicitEnd = typeof line.end === 'number' && line.end > line.time + 0.25;
  const nextGap =
    nextLine !== undefined && nextLine.time > line.time
      ? nextLine.time - line.time
      : null;

  let lineSpan: number;
  const charCount = tokens.reduce((sum, t) => sum + t.length, 0);
  const naturalVocalSec = Math.max(1.5, Math.min(5.2, charCount * 0.085 + tokens.length * 0.1));
  if (hasExplicitEnd) {
    const rawEnd = line.end! - line.time;
    const bounded = nextGap !== null ? Math.min(rawEnd, nextGap * 0.94) : rawEnd;
    lineSpan = Math.min(bounded, Math.max(naturalVocalSec * 1.15, 2.2));
  } else if (nextGap !== null) {
    lineSpan = Math.min(nextGap * 0.92, naturalVocalSec);
  } else {
    lineSpan = naturalVocalSec;
  }
  lineSpan = Math.max(0.4, lineSpan);

  // Weight each word by vowel-group syllable estimate + natural punctuation breath
  const weights = tokens.map((tok) => {
    const vowelGroups = (tok.toLowerCase().match(/[aeiouy]+/g) || []).length;
    const syllables = Math.max(1, vowelGroups, Math.round(tok.length / 3.2));
    const punctPause = /[.!?]$/.test(tok) ? 1.4 : /[,;:]$/.test(tok) ? 0.8 : 0;
    return syllables + tok.length * 0.18 + punctPause;
  });
  const totalWeight = weights.reduce((acc, w) => acc + w, 0);

  let cursor = line.time;
  return tokens.map((tok, idx) => {
    const slice = (weights[idx] / totalWeight) * lineSpan;
    const start = cursor;
    const end = start + Math.max(0.08, slice);
    cursor = end;
    return {
      time: start,
      end,
      text: idx < tokens.length - 1 ? `${tok} ` : tok,
    };
  });
}

export type LyricsDisplayMode = 'word' | 'line';

export interface LyricsControllerState {
  currentTrack: Track | null;
  syncedLines: LyricLine[] | null;
  fallbackWordsByIndex: (LyricWord[] | null)[];
  plainLyrics: string | null;
  lyricsSource: string | null;
  syncType: LyricsSyncType;
  displayMode: LyricsDisplayMode;
  setDisplayMode: (mode: LyricsDisplayMode) => void;
  credit: string | null;
  submitter: string | null;
  songwriters: string[];
  isOfflineLrc: boolean;
  loading: boolean;
  syncOffsetMs: number;
  setSyncOffsetMs: (nextMs: number) => void;
  romanize: boolean;
  toggleRomanize: () => void;
  languageVariants: LyricVariant[];
  selectedVariantCode: string;
  selectVariant: (code: string) => void;
  activeLineIdx: number;
  isAutoScrollPaused: boolean;
  isPlaying: boolean;
  scrollBoxRef: React.RefObject<HTMLDivElement>;
  lineRefs: React.MutableRefObject<(HTMLButtonElement | null)[]>;
  wordSpanRefs: React.MutableRefObject<(HTMLSpanElement | null)[]>;
  lineSweepRef: React.MutableRefObject<HTMLSpanElement | null>;
  fetchLyrics: (opts?: { force?: boolean; silent?: boolean }) => Promise<void>;
  handleResumeAutoScroll: () => void;
  handleCopyAll: () => Promise<void>;
  handleLineSeek: (index: number, targetTimeSec: number) => void;
  handleUserScroll: () => void;
}

interface UseLyricsControllerOptions {
  isActive: boolean;
  isSyncVisible?: boolean;
  playback: PlaybackTick | null;
  queue: QueueData | null;
  onSeek: (seconds: number) => void;
  onSyncScrubber?: (seconds: number) => void;
}

export function useLyricsController({
  isActive,
  isSyncVisible = isActive,
  playback,
  queue,
  onSeek,
  onSyncScrubber,
}: UseLyricsControllerOptions): LyricsControllerState {
  const [rawSyncedLines, setRawSyncedLines] = useState<LyricLine[] | null>(null);
  const [rawPlainLyrics, setRawPlainLyrics] = useState<string | null>(null);
  const [rawVariants, setRawVariants] = useState<LyricVariant[]>([]);
  const [selectedVariantCode, setSelectedVariantCode] = useState<string>('roman');
  const [lyricsSource, setLyricsSource] = useState<string | null>(null);
  const [rawSyncType, setRawSyncType] = useState<LyricsSyncType>('plain');
  const [credit, setCredit] = useState<string | null>(null);
  const [submitter, setSubmitter] = useState<string | null>(null);
  const [songwriters, setSongwriters] = useState<string[]>([]);
  const [isOfflineLrc, setIsOfflineLrc] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [syncOffsetMs, setSyncOffsetMsState] = useState<number>(0);
  const [displayMode, setDisplayModeState] = useState<LyricsDisplayMode>('word');
  const [activeLineIdx, setActiveLineIdx] = useState<number>(-1);
  const [isAutoScrollPaused, setIsAutoScrollPaused] = useState<boolean>(false);

  const scrollBoxRef = useRef<HTMLDivElement>(null);
  const lineRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const wordSpanRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const lineSweepRef = useRef<HTMLSpanElement | null>(null);
  const lastServerPosRef = useRef<number>(0);
  const lastTickRef = useRef<number>(performance.now());
  const lastFrameAtRef = useRef<number>(0);
  const smoothScrollTopRef = useRef<number>(0);
  const seekGuardUntilRef = useRef<number>(0);
  const seekTargetRef = useRef<number>(0);
  const committedActiveIdxRef = useRef<number>(-1);
  const reqSeqRef = useRef<number>(0);
  const fetchedDurRef = useRef<number>(0);
  const durationRefetchedVidRef = useRef<string | null>(null);
  const isAutoScrollPausedRef = useRef<boolean>(false);
  isAutoScrollPausedRef.current = isAutoScrollPaused;

  const currentTrack = playback?.current_track || queue?.current_track || null;
  const currentVideoId = currentTrack?.video_id ?? null;
  const isPlaying = playback?.state === 'playing';
  const liveDuration =
    playback?.duration && playback.duration > 0
      ? playback.duration
      : currentTrack?.duration_s || 0;

  // Build genuine language/script variants (ZERO machine translation):
  // - Uses provider-supplied multi-language variants (e.g. Latin LRC + Hindi LRC, TTML x-translation)
  // - Whenever non-Latin lyrics exist without a provider 'roman' variant, ALWAYS generates the
  //   Romanized Latin variant (using official TTML x-roman / transliteration when present) so the
  //   Romanized Latin version is ALWAYS available and selected by default.
  // - Pure single-language English tracks produce <= 1 variant so NO pill bar is displayed.
  const languageVariants = useMemo<LyricVariant[]>(() => {
    const byCode = new Map<string, LyricVariant>();
    for (const v of rawVariants) {
      if (!v || (!v.synced?.length && !v.lyrics)) continue;
      // Never downgrade a word-synced primary track to a line-only variant from another provider
      if (rawSyncType === 'word' && v.sync_type !== 'word') continue;
      byCode.set(v.code, v);
    }

    const basePlain =
      rawPlainLyrics ||
      (rawSyncedLines
        ? rawSyncedLines
            .filter((l) => l.type !== 'instrumental' && l.text)
            .map((l) => l.text)
            .join('\n')
        : '');

    const detectedNative = basePlain ? detectLyricsLanguage(basePlain) : null;
    const hasNativeScript = Boolean(
      detectedNative ||
        (rawSyncedLines &&
          rawSyncedLines.some(
            (l) => l.type !== 'instrumental' && (l.romanized || hasNonLatinScript(l.text))
          ))
    );

    if (hasNativeScript && (rawSyncedLines?.length || rawPlainLyrics)) {
      const nativeCode = detectedNative?.code || 'orig';
      const nativeLabel = detectedNative?.label || 'Original';

      if (!byCode.has(nativeCode)) {
        byCode.set(nativeCode, {
          code: nativeCode,
          label: nativeLabel,
          script: 'native',
          synced: rawSyncedLines,
          lyrics: rawPlainLyrics,
          sync_type: rawSyncType,
          source: lyricsSource,
        });
      }

      // Always ensure the Romanized Latin script version exists (never blocked by 'en')
      if (!byCode.has('roman')) {
        byCode.set('roman', {
          code: 'roman',
          label: 'Romanized',
          script: 'latin',
          synced: romanizeSyncedLines(rawSyncedLines),
          lyrics: rawPlainLyrics ? romanizeText(rawPlainLyrics) : null,
          sync_type: rawSyncType,
          source: lyricsSource,
        });
      }
    }

    const list = Array.from(byCode.values()).sort(
      (a, b) => variantPriority(a.code) - variantPriority(b.code)
    );
    return list.length > 1 ? list : [];
  }, [rawVariants, rawSyncedLines, rawPlainLyrics, rawSyncType, lyricsSource]);

  const activeVariant = useMemo(() => {
    if (languageVariants.length === 0) return null;
    return (
      languageVariants.find((v) => v.code === selectedVariantCode) ||
      languageVariants.find((v) => v.code === 'roman') ||
      languageVariants.find((v) => v.script === 'latin') ||
      languageVariants[0]
    );
  }, [languageVariants, selectedVariantCode]);

  const syncedLines = activeVariant ? activeVariant.synced : rawSyncedLines;
  const plainLyrics = activeVariant ? activeVariant.lyrics : rawPlainLyrics;
  const syncType: LyricsSyncType = activeVariant?.sync_type || rawSyncType;
  const romanize = Boolean(activeVariant && activeVariant.script === 'latin');

  // Duration-locked word glide for lines lacking explicit provider word timestamps
  const fallbackWordsByIndex = useMemo(() => {
    if (!syncedLines) return [] as (LyricWord[] | null)[];
    return syncedLines.map((line, i) => {
      if (line.type === 'instrumental') return null;
      if (line.words && line.words.length > 0) return null;
      return buildDurationLockedWords(line, syncedLines[i + 1]);
    });
  }, [syncedLines]);

  const currentTrackRef = useRef<Track | null>(currentTrack);
  currentTrackRef.current = currentTrack;
  const currentVideoIdRef = useRef<string | null>(currentVideoId);
  currentVideoIdRef.current = currentVideoId;
  const liveDurationRef = useRef<number>(liveDuration);
  liveDurationRef.current = liveDuration;
  const syncedLinesRef = useRef<LyricLine[] | null>(syncedLines);
  syncedLinesRef.current = syncedLines;
  const fallbackWordsRef = useRef<(LyricWord[] | null)[]>(fallbackWordsByIndex);
  fallbackWordsRef.current = fallbackWordsByIndex;
  const plainLyricsRef = useRef<string | null>(plainLyrics);
  plainLyricsRef.current = plainLyrics;
  const syncOffsetMsRef = useRef<number>(syncOffsetMs);
  syncOffsetMsRef.current = syncOffsetMs;
  const isPlayingRef = useRef<boolean>(isPlaying);
  isPlayingRef.current = isPlaying;
  const displayModeRef = useRef<LyricsDisplayMode>(displayMode);
  displayModeRef.current = displayMode;
  const activeLineIdxRef = useRef<number>(activeLineIdx);
  activeLineIdxRef.current = activeLineIdx;

  // Reset tick anchor whenever playback resumes so pause/buffer time never leaks into elapsed
  useEffect(() => {
    if (isPlaying) {
      lastTickRef.current = performance.now();
    }
  }, [isPlaying]);

  const setSyncOffsetMs = useCallback((nextMs: number) => {
    const clamped = Math.max(MIN_OFFSET_MS, Math.min(MAX_OFFSET_MS, Math.round(nextMs)));
    setSyncOffsetMsState(clamped);
    syncOffsetMsRef.current = clamped;
    saveOffsetMs(currentVideoIdRef.current, clamped);
  }, []);

  const setDisplayMode = useCallback((mode: LyricsDisplayMode) => {
    setDisplayModeState(mode);
    displayModeRef.current = mode;
  }, []);

  const selectVariant = useCallback((code: string) => {
    setSelectedVariantCode(code);
  }, []);

  const toggleRomanize = useCallback(() => {
    if (languageVariants.length < 2) return;
    const latin =
      languageVariants.find((v) => v.code === 'roman') ||
      languageVariants.find((v) => v.script === 'latin');
    const native = languageVariants.find((v) => v.script === 'native');
    if (!latin || !native) return;
    setSelectedVariantCode((prev) => (prev === latin.code ? native.code : latin.code));
  }, [languageVariants]);

  const getEstimatedPos = useCallback((): number => {
    const rawElapsed = isPlayingRef.current
      ? (performance.now() - lastTickRef.current) / 1000
      : 0;
    const clampedElapsed = Math.min(MAX_INTERP_ELAPSED_SEC, Math.max(0, rawElapsed));
    return lastServerPosRef.current + clampedElapsed + syncOffsetMsRef.current / 1000 + SYNC_LEAD_SEC;
  }, []);

  const paintActiveProgress = useCallback((pos: number, idx: number) => {
    if (displayModeRef.current !== 'word') return;
    const lines = syncedLinesRef.current;
    if (!lines || idx < 0 || idx >= lines.length) return;
    const line = lines[idx];
    if (!line || line.type === 'instrumental') return;

    const words =
      line.words && line.words.length > 0 ? line.words : fallbackWordsRef.current[idx];
    if (words && words.length > 0) {
      const spans = wordSpanRefs.current;
      for (let wIdx = 0; wIdx < words.length; wIdx++) {
        const span = spans[wIdx];
        if (!span) continue;
        const w = words[wIdx];
        if (pos >= w.end) {
          span.classList.add('sung');
          span.classList.remove('unsung');
          span.style.removeProperty('--wp');
        } else if (pos <= w.time) {
          span.classList.add('unsung');
          span.classList.remove('sung');
          span.style.removeProperty('--wp');
        } else {
          span.classList.remove('sung', 'unsung');
          const pct = Math.min(
            100,
            Math.max(0, ((pos - w.time) / Math.max(0.05, w.end - w.time)) * 100)
          );
          span.style.setProperty('--wp', `${pct.toFixed(1)}%`);
        }
      }
      return;
    }
  }, []);

  const glideScrollTop = useCallback((targetTop: number, instant: boolean, dt: number, stiffness: number) => {
    const box = scrollBoxRef.current;
    if (!box) return;
    const maxScroll = Math.max(0, box.scrollHeight - box.clientHeight);
    const clamped = Math.min(maxScroll, Math.max(0, targetTop));
    if (instant) {
      smoothScrollTopRef.current = clamped;
      box.scrollTop = clamped;
      return;
    }
    if (isAutoScrollPausedRef.current) return;
    // Track scroll position as a float so sub-pixel steps never stall on Windows DPI scaling
    if (Math.abs(smoothScrollTopRef.current - box.scrollTop) > 2.5) {
      smoothScrollTopRef.current = box.scrollTop;
    }
    const diff = clamped - smoothScrollTopRef.current;
    if (Math.abs(diff) > 0.25) {
      const alpha = 1 - Math.exp(-stiffness * dt);
      smoothScrollTopRef.current += diff * alpha;
      box.scrollTop = smoothScrollTopRef.current;
    }
  }, []);

  const glideToLine = useCallback(
    (idx: number, instant: boolean, dt: number) => {
      const box = scrollBoxRef.current;
      const el = lineRefs.current[idx];
      if (!box || !el) return;
      const offsetTop = getOffsetTopInContainer(el, box);
      const targetTop = offsetTop + el.offsetHeight * 0.5 - box.clientHeight * 0.44;
      glideScrollTop(targetTop, instant, dt, 7.8);
    },
    [glideScrollTop]
  );

  const syncFrame = useCallback(
    (instant = false, dt = 0.016) => {
      if (document.hidden) return;
      const lines = syncedLinesRef.current;
      const pos = getEstimatedPos();

      // Plain lyrics fallback: smooth proportional auto-scroll
      if (!lines || lines.length === 0) {
        const box = scrollBoxRef.current;
        const dur = liveDurationRef.current;
        if (box && plainLyricsRef.current && dur > 5) {
          const maxScroll = Math.max(0, box.scrollHeight - box.clientHeight);
          if (maxScroll > 0) {
            const ratio = Math.min(1, Math.max(0, (pos - SYNC_LEAD_SEC) / dur));
            glideScrollTop(ratio * maxScroll, instant, dt, 4.5);
          }
        }
        return;
      }

      let idx = -1;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].time <= pos) {
          idx = i;
        } else {
          break;
        }
      }

      if (idx !== activeLineIdxRef.current) {
        activeLineIdxRef.current = idx;
        setActiveLineIdx(idx);
      }

      if (idx >= 0 && idx === committedActiveIdxRef.current) {
        paintActiveProgress(pos, idx);
      }
      glideToLine(idx >= 0 ? idx : 0, instant, dt);
    },
    [getEstimatedPos, paintActiveProgress, glideToLine, glideScrollTop]
  );

  // Reset local time anchor on track change or stage activation
  useEffect(() => {
    if (!isActive) return;
    const hasFreshSocket = Boolean(
      currentVideoId && bridgeSocket.latestTrackId === currentVideoId && bridgeSocket.latestTickAt > 0
    );
    const syncedPos = hasFreshSocket ? bridgeSocket.latestTimePos : playback?.time_pos ?? 0;
    lastServerPosRef.current = syncedPos;
    lastTickRef.current = hasFreshSocket ? bridgeSocket.latestTickAt : performance.now();
    lastFrameAtRef.current = 0;
    seekGuardUntilRef.current = 0;
    lineRefs.current = [];
    wordSpanRefs.current = [];
    setIsAutoScrollPaused(false);
    isAutoScrollPausedRef.current = false;
  }, [currentVideoId, isActive]);

  // Direct WebSocket playback_tick & track_changed listeners
  useEffect(() => {
    if (!isSyncVisible) return;
    const unsubTick = bridgeSocket.on('playback_tick', (tick) => {
      const tickVid = tick.current_track?.video_id ?? null;
      if (currentVideoIdRef.current && tickVid && tickVid !== currentVideoIdRef.current) {
        return;
      }
      if (typeof tick.state === 'string') {
        isPlayingRef.current = tick.state === 'playing';
      }
      if (typeof tick.time_pos === 'number') {
        const now = performance.now();
        if (
          now < seekGuardUntilRef.current &&
          Math.abs(tick.time_pos - seekTargetRef.current) > 1.5
        ) {
          return;
        }
        // Smooth phase-locked clock: gently blend tiny WS jitter (<140ms) so active word sweep
        // never stutters or jumps backwards; snap immediately on real seeks or pauses.
        if (isPlayingRef.current) {
          const rawElapsed = Math.min(
            MAX_INTERP_ELAPSED_SEC,
            Math.max(0, (now - lastTickRef.current) / 1000)
          );
          const currentEst = lastServerPosRef.current + rawElapsed;
          const err = tick.time_pos - currentEst;
          if (Math.abs(err) < 0.14) {
            lastServerPosRef.current = currentEst + err * 0.35;
          } else {
            lastServerPosRef.current = tick.time_pos;
          }
        } else {
          lastServerPosRef.current = tick.time_pos;
        }
        lastTickRef.current = now;
        if (!isPlayingRef.current) {
          syncFrame(false, 0.14);
        }
      }
    });
    const unsubTrack = bridgeSocket.on('track_changed', (evt) => {
      const nextPos = typeof evt.time_pos === 'number' ? evt.time_pos : 0;
      if (typeof evt.state === 'string') {
        isPlayingRef.current = evt.state === 'playing';
      }
      lastServerPosRef.current = nextPos;
      lastTickRef.current = performance.now();
      seekGuardUntilRef.current = 0;
      syncFrame(true, 0.016);
    });
    return () => {
      unsubTick();
      unsubTrack();
    };
  }, [isSyncVisible, syncFrame]);

  const fetchLyrics = useCallback(
    async (opts?: { force?: boolean; silent?: boolean }) => {
      const trk = currentTrackRef.current;
      if (!currentVideoId || !trk) return;

      const mySeq = ++reqSeqRef.current;
      setLoading(true);
      setRawSyncedLines(null);
      setRawPlainLyrics(null);
      setRawVariants([]);
      setLyricsSource(null);
      setRawSyncType('plain');
      setCredit(null);
      setSubmitter(null);
      setSongwriters([]);
      setIsOfflineLrc(false);
      setActiveLineIdx(-1);
      activeLineIdxRef.current = -1;
      committedActiveIdxRef.current = -1;
      setIsAutoScrollPaused(false);
      isAutoScrollPausedRef.current = false;
      lineRefs.current = [];
      wordSpanRefs.current = [];

      const effDur =
        liveDurationRef.current > 0
          ? liveDurationRef.current
          : bridgeSocket.latestTrackId === currentVideoId && bridgeSocket.latestDuration > 0
          ? bridgeSocket.latestDuration
          : trk.duration_s;
      fetchedDurRef.current = effDur || 0;

      try {
        const res = await bridgeApi.getLyrics(currentVideoId, {
          title: trk.title,
          artist: trk.artist,
          duration: effDur,
          force: opts?.force,
        });
        if (mySeq !== reqSeqRef.current) return;
        // Re-anchor to latest live audio position right as lyrics finish loading
        if (bridgeSocket.latestTrackId === currentVideoId && bridgeSocket.latestTickAt > 0) {
          lastServerPosRef.current = bridgeSocket.latestTimePos;
          lastTickRef.current = bridgeSocket.latestTickAt;
        }
        const lines = res.synced && res.synced.length > 0 ? res.synced : null;
        const variants = Array.isArray(res.variants) ? res.variants : [];
        const primarySync: LyricsSyncType = res.sync_type || (lines ? 'line' : 'plain');
        const syncRank = (st?: LyricsSyncType) => (st === 'word' ? 3 : st === 'line' ? 2 : 1);
        const eligibleVariants = variants.filter(
          (v) => syncRank(v.sync_type) >= syncRank(primarySync)
        );
        // Default to Word Sync for genuine word-timed tracks, and clean Line Sync for line-only
        // providers (like LRCLIB) so line-only tracks never suffer from synthetic mid-line drift.
        const nextMode: LyricsDisplayMode = primarySync === 'word' ? 'word' : 'line';
        setDisplayModeState(nextMode);
        displayModeRef.current = nextMode;
        setRawSyncedLines(lines);
        setRawPlainLyrics(res.lyrics || null);
        setRawVariants(eligibleVariants);
        // Always default to Romanized Latin of EQUAL OR BETTER sync tier (never downgrade word -> line)
        const romanVariant = eligibleVariants.find((v) => v.code === 'roman');
        const defaultLatin = romanVariant || eligibleVariants.find((v) => v.script === 'latin');
        setSelectedVariantCode(defaultLatin ? defaultLatin.code : 'roman');
        setLyricsSource(res.source || null);
        setRawSyncType(primarySync);
        setCredit(res.credit || res.source || null);
        setSubmitter(res.submitter || null);
        setSongwriters(Array.isArray(res.songwriters) ? res.songwriters : []);
        setIsOfflineLrc(Boolean(res.offline));
        if (opts?.force && !opts?.silent) {
          toast.show(
            lines || res.lyrics ? 'Lyrics refreshed' : 'Lyrics not available',
            lines || res.lyrics ? 'success' : 'info'
          );
        }
      } catch {
        if (mySeq !== reqSeqRef.current) return;
        setRawSyncedLines(null);
        setRawPlainLyrics(null);
        if (opts?.force && !opts?.silent) {
          toast.show('Failed to refetch lyrics', 'error');
        }
      } finally {
        if (mySeq === reqSeqRef.current) setLoading(false);
      }
    },
    [currentVideoId]
  );

  // Load persisted offset & lyrics whenever active track changes
  useEffect(() => {
    if (!isActive || !currentVideoId) return;
    durationRefetchedVidRef.current = null;
    const saved = loadOffsetMs(currentVideoId);
    setSyncOffsetMsState(saved);
    syncOffsetMsRef.current = saved;
    void fetchLyrics();
  }, [isActive, currentVideoId, fetchLyrics]);

  // If real libmpv duration settles >3.5s away from initial catalog duration (or initial fetch
  // ran without a valid duration), re-resolve once with exact stream duration so alignment locks.
  useEffect(() => {
    if (!isActive || !currentVideoId || loading || isOfflineLrc) return;
    const settled = playback?.duration || 0;
    if (settled <= 10) return;
    if (durationRefetchedVidRef.current === currentVideoId) return;
    const prevDur = fetchedDurRef.current;
    if (prevDur <= 10 || Math.abs(settled - prevDur) > 3.5) {
      durationRefetchedVidRef.current = currentVideoId;
      void fetchLyrics({ force: true, silent: true });
    }
  }, [isActive, currentVideoId, playback?.duration, loading, isOfflineLrc, fetchLyrics]);

  // 60fps RAF loop for silky word-by-word sweep & smooth scroll glide
  // (only pauses when hidden/minimized — stays butter-smooth even when window is unfocused on screen)
  const hasAnyLyrics = Boolean((syncedLines && syncedLines.length > 0) || plainLyrics);
  useEffect(() => {
    if (!isSyncVisible || !hasAnyLyrics) return;

    syncFrame(true, 0.016);
    if (!isPlaying) return;

    let rafId = 0;
    const frame = (now: number) => {
      if (document.hidden) {
        rafId = 0;
        lastFrameAtRef.current = 0;
        return;
      }
      const dt = lastFrameAtRef.current
        ? Math.min(0.05, Math.max(0.004, (now - lastFrameAtRef.current) / 1000))
        : 0.016;
      lastFrameAtRef.current = now;
      syncFrame(false, dt);
      rafId = requestAnimationFrame(frame);
    };

    const syncLoop = () => {
      if (document.hidden) {
        if (rafId !== 0) {
          cancelAnimationFrame(rafId);
          rafId = 0;
          lastFrameAtRef.current = 0;
        }
      } else if (rafId === 0) {
        lastFrameAtRef.current = 0;
        rafId = requestAnimationFrame(frame);
      }
    };

    syncLoop();
    document.addEventListener('visibilitychange', syncLoop);
    return () => {
      if (rafId !== 0) cancelAnimationFrame(rafId);
      lastFrameAtRef.current = 0;
      document.removeEventListener('visibilitychange', syncLoop);
    };
  }, [isSyncVisible, hasAnyLyrics, isPlaying, syncOffsetMs, selectedVariantCode, syncFrame]);

  // Synchronously paint newly committed word/sweep spans & keep paused view centered
  useLayoutEffect(() => {
    committedActiveIdxRef.current = activeLineIdx;
    if (!isSyncVisible || !syncedLines || syncedLines.length === 0 || activeLineIdx < 0) return;
    paintActiveProgress(getEstimatedPos(), activeLineIdx);
    if (!isPlayingRef.current) {
      glideToLine(activeLineIdx, true, 0.016);
    }
  }, [
    isSyncVisible,
    syncedLines,
    activeLineIdx,
    selectedVariantCode,
    displayMode,
    syncOffsetMs,
    getEstimatedPos,
    paintActiveProgress,
    glideToLine,
  ]);

  const handleResumeAutoScroll = useCallback(() => {
    isAutoScrollPausedRef.current = false;
    setIsAutoScrollPaused(false);
    syncFrame(false, 0.08);
  }, [syncFrame]);

  const handleUserScroll = useCallback(() => {
    if (scrollBoxRef.current) {
      smoothScrollTopRef.current = scrollBoxRef.current.scrollTop;
    }
    if ((syncedLinesRef.current && syncedLinesRef.current.length > 0) || plainLyricsRef.current) {
      isAutoScrollPausedRef.current = true;
      setIsAutoScrollPaused(true);
    }
  }, []);

  const handleLineSeek = useCallback(
    (index: number, targetTimeSec: number) => {
      isAutoScrollPausedRef.current = false;
      setIsAutoScrollPaused(false);
      const target = Math.max(0, targetTimeSec - syncOffsetMsRef.current / 1000);
      const now = performance.now();
      seekTargetRef.current = target;
      seekGuardUntilRef.current = now + 850;
      lastServerPosRef.current = target;
      lastTickRef.current = now;
      activeLineIdxRef.current = index;
      setActiveLineIdx(index);
      onSyncScrubber?.(target);
      onSeek(target);
    },
    [onSeek, onSyncScrubber]
  );

  const handleCopyAll = useCallback(async () => {
    let rawText = '';
    if (syncedLinesRef.current && syncedLinesRef.current.length > 0) {
      rawText = syncedLinesRef.current
        .filter((l) => l.type !== 'instrumental' && l.text.trim().length > 0)
        .map((l) => l.text)
        .join('\n');
    } else if (plainLyricsRef.current) {
      rawText = plainLyricsRef.current;
    }

    if (!rawText.trim()) {
      toast.show('No lyrics available to copy', 'info');
      return;
    }

    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(rawText);
      } else {
        const ta = document.createElement('textarea');
        ta.value = rawText;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      toast.show('Lyrics copied to clipboard', 'success');
    } catch {
      toast.show('Failed to copy lyrics', 'error');
    }
  }, []);

  return {
    currentTrack,
    syncedLines,
    fallbackWordsByIndex,
    plainLyrics,
    lyricsSource: activeVariant?.source || lyricsSource,
    syncType,
    displayMode,
    setDisplayMode,
    credit: activeVariant?.source || credit,
    submitter,
    songwriters,
    isOfflineLrc,
    loading,
    syncOffsetMs,
    setSyncOffsetMs,
    romanize,
    toggleRomanize,
    languageVariants,
    selectedVariantCode: activeVariant?.code || selectedVariantCode,
    selectVariant,
    activeLineIdx,
    isAutoScrollPaused,
    isPlaying,
    scrollBoxRef,
    lineRefs,
    wordSpanRefs,
    lineSweepRef,
    fetchLyrics,
    handleResumeAutoScroll,
    handleCopyAll,
    handleLineSeek,
    handleUserScroll,
  };
}

// ---------------------------------------------------------------------------
// 2-Tab Sync Mode Switcher (`Word Sync` | `Line Sync`)
// ---------------------------------------------------------------------------
interface LyricsSyncModeTabsProps {
  controller: LyricsControllerState;
  compact?: boolean;
}

export const LyricsSyncModeTabs = ({ controller, compact = false }: LyricsSyncModeTabsProps) => {
  if (!controller.syncedLines || controller.syncedLines.length === 0) return null;
  const isWord = controller.displayMode === 'word';

  return (
    <div
      role="tablist"
      aria-label="Lyrics sync mode"
      className={`inline-flex items-center p-0.5 rounded-full bg-white/[0.06] border border-white/[0.11] flex-none ${
        compact ? 'text-[9px]' : 'text-[10px]'
      } font-mono uppercase`}
    >
      <button
        type="button"
        role="tab"
        aria-selected={isWord}
        onClick={() => controller.setDisplayMode('word')}
        className={`rounded-full font-semibold whitespace-nowrap transition-all ${
          compact ? 'px-2 py-0.5' : 'px-2.5 py-0.5'
        } ${
          isWord
            ? 'bg-white/[0.15] text-white'
            : 'text-[#8E99BA] hover:text-white'
        }`}
        style={
          isWord
            ? {
                color: 'var(--dynamic-accent)',
                boxShadow: 'inset 0 0 0 1px rgba(var(--dynamic-accent-rgb), 0.45)',
              }
            : undefined
        }
      >
        Word Sync
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={!isWord}
        onClick={() => controller.setDisplayMode('line')}
        className={`rounded-full font-semibold whitespace-nowrap transition-all ${
          compact ? 'px-2 py-0.5' : 'px-2.5 py-0.5'
        } ${
          !isWord
            ? 'bg-white/[0.15] text-white'
            : 'text-[#8E99BA] hover:text-white'
        }`}
        style={
          !isWord
            ? {
                color: 'var(--dynamic-accent)',
                boxShadow: 'inset 0 0 0 1px rgba(var(--dynamic-accent-rgb), 0.45)',
              }
            : undefined
        }
      >
        Line Sync
      </button>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Compact `⋮` Overflow Menu (Refetch, Copy All, Offset Slider, Romanize)
// ---------------------------------------------------------------------------
interface LyricsOverflowMenuProps {
  controller: LyricsControllerState;
}

export const LyricsOverflowMenu = ({ controller }: LyricsOverflowMenuProps) => {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setOpen(false);
      }
    };
    window.addEventListener('mousedown', onPointerDown);
    window.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.removeEventListener('mousedown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);

  const hasLyrics = Boolean(
    (controller.syncedLines && controller.syncedLines.length > 0) ||
      (controller.plainLyrics && controller.plainLyrics.trim().length > 0)
  );
  const hasSynced = Boolean(controller.syncedLines && controller.syncedLines.length > 0);
  const canToggleScript =
    controller.languageVariants.some((v) => v.script === 'latin') &&
    controller.languageVariants.some((v) => v.script === 'native');

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Lyrics options"
        aria-expanded={open}
        title="Lyrics Options"
        className={`p-1.5 rounded-full transition-colors ${
          open
            ? 'bg-white/[0.14] text-white'
            : 'text-[#A6B0CF] hover:text-white hover:bg-white/[0.10]'
        }`}
      >
        <MoreVertical size={14} />
      </button>

      {open && (
        <div
          role="menu"
          className="eh-popover-in absolute right-0 top-9 z-50 w-64 rounded-2xl ios-liquid-popover backdrop-blur-md p-2 text-xs text-[#F1F4FA] space-y-1"
        >
          <button
            type="button"
            role="menuitem"
            disabled={controller.loading || !controller.currentTrack}
            onClick={() => {
              setOpen(false);
              void controller.fetchLyrics({ force: true });
            }}
            className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl hover:bg-white/[0.08] disabled:opacity-40 transition-colors text-left"
          >
            <RefreshCw size={13} className={controller.loading ? 'animate-spin' : 'text-[#A6B0CF]'} />
            <span className="flex-1 font-medium">Refetch Lyrics</span>
          </button>

          <button
            type="button"
            role="menuitem"
            disabled={!hasLyrics}
            onClick={() => {
              setOpen(false);
              void controller.handleCopyAll();
            }}
            className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl hover:bg-white/[0.08] disabled:opacity-40 transition-colors text-left"
          >
            <Copy size={13} className="text-[#A6B0CF]" />
            <span className="flex-1 font-medium">Copy All</span>
          </button>

          {canToggleScript && (
            <button
              type="button"
              role="menuitemcheckbox"
              aria-checked={controller.romanize}
              onClick={controller.toggleRomanize}
              className="w-full flex items-center justify-between gap-2.5 px-2.5 py-2 rounded-xl hover:bg-white/[0.08] transition-colors text-left"
            >
              <span className="flex items-center gap-2.5">
                <Languages
                  size={13}
                  style={controller.romanize ? { color: 'var(--dynamic-accent)' } : undefined}
                  className={controller.romanize ? '' : 'text-[#A6B0CF]'}
                />
                <span className="font-medium">Romanize Script</span>
              </span>
              <span
                className={`w-7 h-4 rounded-full p-0.5 transition-colors flex items-center ${
                  controller.romanize ? 'bg-[var(--dynamic-accent)]' : 'bg-white/15'
                }`}
              >
                <span
                  className={`w-3 h-3 rounded-full bg-white transition-transform ${
                    controller.romanize ? 'translate-x-3' : 'translate-x-0'
                  }`}
                />
              </span>
            </button>
          )}

          {hasSynced && (
            <div className="mt-1 pt-2 px-2.5 pb-1.5 border-t border-white/[0.10] space-y-2">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-[#9AA5C4] font-semibold">
                  <SlidersHorizontal size={11} />
                  Sync Offset
                </span>
                <div className="flex items-center gap-1">
                  <span
                    className="text-[11px] font-mono tabular-nums font-semibold"
                    style={{
                      color: controller.syncOffsetMs !== 0 ? 'var(--dynamic-accent)' : '#E6ECF8',
                    }}
                  >
                    {formatOffsetMs(controller.syncOffsetMs)}
                  </span>
                  {controller.syncOffsetMs !== 0 && (
                    <button
                      type="button"
                      onClick={() => controller.setSyncOffsetMs(0)}
                      title="Reset sync offset"
                      className="p-0.5 rounded-full hover:bg-white/[0.12] text-[#9AA5C4] hover:text-white"
                    >
                      <RotateCcw size={10} />
                    </button>
                  )}
                </div>
              </div>

              <input
                type="range"
                min={MIN_OFFSET_MS}
                max={MAX_OFFSET_MS}
                step={50}
                value={controller.syncOffsetMs}
                onChange={(e) => controller.setSyncOffsetMs(Number(e.target.value))}
                aria-label="Lyrics sync offset in milliseconds"
                className="w-full orbit-slider cursor-pointer"
              />

              <div className="flex items-center justify-between gap-1 text-[10px] font-mono text-[#8E99BA]">
                <button
                  type="button"
                  onClick={() => controller.setSyncOffsetMs(controller.syncOffsetMs - 100)}
                  className="px-1.5 py-0.5 rounded-md bg-white/[0.06] hover:bg-white/[0.14] text-[#E6ECF8] transition-colors"
                >
                  -100ms
                </button>
                <span className="text-[9px] text-[#6D7796]">Saved per track</span>
                <button
                  type="button"
                  onClick={() => controller.setSyncOffsetMs(controller.syncOffsetMs + 100)}
                  className="px-1.5 py-0.5 rounded-md bg-white/[0.06] hover:bg-white/[0.14] text-[#E6ECF8] transition-colors"
                >
                  +100ms
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Shared Lyrics Stage Canvas (Word Sync + Word-Paced Line Sync + Genuine Language Pills)
// ---------------------------------------------------------------------------
interface LyricsStageBodyProps {
  variant: 'drawer' | 'fullscreen';
  controller: LyricsControllerState;
  lyricsEdgeBlur: boolean;
}

export const LyricsStageBody = ({
  variant,
  controller,
  lyricsEdgeBlur,
}: LyricsStageBodyProps) => {
  const {
    currentTrack,
    syncedLines,
    fallbackWordsByIndex,
    plainLyrics,
    lyricsSource,
    displayMode,
    credit,
    submitter,
    songwriters,
    loading,
    activeLineIdx,
    isAutoScrollPaused,
    isPlaying,
    scrollBoxRef,
    lineRefs,
    wordSpanRefs,
    handleResumeAutoScroll,
    handleLineSeek,
    handleUserScroll,
  } = controller;

  const isFullscreen = variant === 'fullscreen';
  const isWordMode = displayMode === 'word';

  const plainLines = useMemo(() => {
    if (!plainLyrics) return [];
    return plainLyrics.split(/\r?\n/);
  }, [plainLyrics]);

  return (
    <>
      <div className="flex-1 min-h-0 relative overflow-hidden">
        <div
          ref={scrollBoxRef}
          onWheel={handleUserScroll}
          onTouchMove={handleUserScroll}
          className={`relative h-full overflow-y-auto no-scrollbar lyrics-fade-mask ${
            isFullscreen ? 'px-6 lg:px-12' : 'px-5'
          }`}
        >
          {!currentTrack ? (
            <div className="h-full flex items-center justify-center text-xs text-[#646E8F] text-center">
              Play any track to view synced lyrics.
            </div>
          ) : loading ? (
            <LiquidGlassLoader />
          ) : syncedLines && syncedLines.length > 0 ? (
            <div
              className={isFullscreen ? 'space-y-4 max-w-xl mx-auto' : 'space-y-3.5'}
              style={{
                paddingTop: isFullscreen ? '34%' : '36%',
                paddingBottom: isFullscreen ? '46%' : '48%',
              }}
            >
              {syncedLines.map((line, i) => {
                const dist = Math.abs(i - activeLineIdx);
                const isActive = i === activeLineIdx;
                const isPast = i < activeLineIdx;
                const isRight = line.align === 'right';
                const isInstrumental = line.type === 'instrumental';

                const opacity = isActive
                  ? 1
                  : dist === 1
                  ? isPast
                    ? 0.44
                    : 0.68
                  : dist === 2
                  ? 0.34
                  : 0.22;

                const blurClass =
                  lyricsEdgeBlur && !isActive
                    ? dist === 2
                      ? 'eh-lyric-blur-2'
                      : dist >= 3
                      ? 'eh-lyric-blur-3'
                      : ''
                    : '';

                const shiftPx = isFullscreen ? 8 : 4;
                const activeTransform = isRight
                  ? `translate3d(-${shiftPx}px, 0, 0) scale(${isFullscreen ? '1.05' : '1.04'})`
                  : `translate3d(${shiftPx}px, 0, 0) scale(${isFullscreen ? '1.05' : '1.04'})`;

                if (isInstrumental) {
                  return (
                    <button
                      key={`inst-${line.time}-${i}`}
                      ref={(el) => {
                        lineRefs.current[i] = el;
                      }}
                      type="button"
                      onClick={() => handleLineSeek(i, line.time)}
                      aria-label="Instrumental interlude"
                      style={{
                        opacity: isActive ? 1 : Math.min(opacity, 0.36),
                        transform: isActive ? 'scale(1.04)' : 'scale(0.92)',
                        transformOrigin: 'left center',
                        transition:
                          'transform 260ms cubic-bezier(0.22, 1, 0.36, 1), opacity 220ms ease',
                      }}
                      className={`flex items-center gap-2 py-2 px-1.5 rounded-xl eh-lyric-line ${blurClass}`}
                    >
                      {[0, 1, 2].map((dotIdx) => (
                        <span
                          key={dotIdx}
                          className={`rounded-full eh-orbital-dot ${
                            isFullscreen ? 'w-2.5 h-2.5' : 'w-2 h-2'
                          } ${isActive && isPlaying ? '' : 'eh-paused'}`}
                          style={{
                            animationDelay: `${dotIdx * 210}ms`,
                            background: isActive ? 'var(--dynamic-accent)' : '#9AA5C4',
                            boxShadow: isActive
                              ? '0 0 12px rgba(var(--dynamic-accent-rgb), 0.72)'
                              : 'none',
                          }}
                        />
                      ))}
                    </button>
                  );
                }

                const exactWords = line.words && line.words.length > 0 ? line.words : null;
                const renderWords = isWordMode ? exactWords || fallbackWordsByIndex[i] : null;
                const hasWords = Boolean(isWordMode && renderWords && renderWords.length > 0);
                const hasExplicitSpaces = Boolean(
                  renderWords && renderWords.some((w) => /\s$/.test(w.text))
                );
                if (isActive) {
                  wordSpanRefs.current.length = hasWords && renderWords ? renderWords.length : 0;
                }

                return (
                  <button
                    key={`${line.time}-${i}`}
                    ref={(el) => {
                      lineRefs.current[i] = el;
                    }}
                    type="button"
                    onClick={() => handleLineSeek(i, line.time)}
                    style={{
                      opacity,
                      transform: isActive
                        ? activeTransform
                        : 'translate3d(0, 0, 0) scale(0.95)',
                      transformOrigin: isRight ? 'right center' : 'left center',
                      transition:
                        'transform 340ms cubic-bezier(0.22, 1, 0.36, 1), opacity 280ms ease, filter 260ms ease',
                      willChange: isActive || dist <= 2 ? 'transform, opacity' : 'auto',
                    }}
                    className={`block w-full rounded-xl leading-snug font-bold break-words [overflow-wrap:anywhere] eh-lyric-line ${
                      isActive && hasWords ? 'eh-lyr-active-karaoke' : ''
                    } ${blurClass} ${isRight ? 'text-right pl-6' : 'text-left pr-4'} ${
                      isFullscreen
                        ? 'px-2 py-1 text-2xl lg:text-[27px] tracking-[-0.015em]'
                        : 'px-1.5 py-0.5 text-[17px] tracking-[-0.012em]'
                    } ${
                      isActive
                        ? isRight
                          ? 'text-[#EEF2FF]'
                          : 'text-white'
                        : isRight
                        ? 'text-[#C8D4FA]'
                        : 'text-[#D6DDF5]'
                    }`}
                  >
                    {isActive && hasWords && renderWords ? (
                      <span className="inline">
                        {renderWords.map((word, wIdx) => {
                          const displayWord = hasExplicitSpaces
                            ? word.text
                            : wIdx < renderWords.length - 1
                            ? `${word.text} `
                            : word.text;
                          return (
                            <span
                              key={`${line.time}-${wIdx}`}
                              ref={(el) => {
                                wordSpanRefs.current[wIdx] = el;
                              }}
                              onClick={(e) => {
                                if (!exactWords) return;
                                e.stopPropagation();
                                handleLineSeek(i, word.time);
                              }}
                              className="eh-lyr-word cursor-pointer"
                            >
                              {displayWord}
                            </span>
                          );
                        })}
                      </span>
                    ) : (
                      <span
                        style={
                          isActive
                            ? {
                                textShadow: isRight
                                  ? '0 0 18px rgba(var(--dynamic-secondary-rgb), 0.35)'
                                  : '0 0 16px rgba(255, 255, 255, 0.24)',
                              }
                            : undefined
                        }
                      >
                        {line.text || '♪'}
                      </span>
                    )}
                  </button>
                );
              })}

              {(credit || lyricsSource || songwriters.length > 0) && (
                <div
                  className={
                    isFullscreen
                      ? 'pt-8 space-y-1 text-[11px] font-mono text-[#646E8F]'
                      : 'pt-6 space-y-1 text-[10px] font-mono text-[#646E8F]'
                  }
                >
                  {(credit || lyricsSource) && (
                    <p>
                      Lyrics via {credit || lyricsSource}
                      {submitter ? ` • ${submitter}` : ''}
                    </p>
                  )}
                  {songwriters.length > 0 && (
                    <p className="text-[#56607E]">Written by {songwriters.join(', ')}</p>
                  )}
                </div>
              )}
            </div>
          ) : plainLines.length > 0 ? (
            <div
              className={
                isFullscreen
                  ? 'space-y-3 pt-10 pb-16 max-w-xl mx-auto'
                  : 'space-y-2.5 pt-8 pb-14'
              }
            >
              {plainLines.map((line, i) => (
                <p
                  key={i}
                  className={
                    line.trim()
                      ? isFullscreen
                        ? 'text-lg leading-relaxed font-medium text-[#F4F6FF]/85'
                        : 'text-sm leading-relaxed font-medium text-[#F4F6FF]/85'
                      : isFullscreen
                      ? 'h-3'
                      : 'h-2.5'
                  }
                >
                  {line}
                </p>
              ))}
              {(credit || lyricsSource) && (
                <p
                  className={
                    isFullscreen
                      ? 'pt-6 text-[11px] font-mono text-[#646E8F] border-t border-white/[0.08]'
                      : 'pt-5 text-[10px] font-mono text-[#646E8F] border-t border-white/[0.08]'
                  }
                >
                  Lyrics via {credit || lyricsSource}
                </p>
              )}
            </div>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-center text-[#A6B0CF] gap-2.5 px-4">
              <AlignLeft size={isFullscreen ? 28 : 24} className="opacity-25" />
              <p
                className={
                  isFullscreen
                    ? 'text-sm font-medium text-[#8E99BA]'
                    : 'text-xs font-medium text-[#8E99BA]'
                }
              >
                Not available
              </p>
            </div>
          )}
        </div>

        {/* Floating Auto scroll pill (works for both synced & plain auto-scrolling lyrics) */}
        {isAutoScrollPaused && ((syncedLines && syncedLines.length > 0) || plainLines.length > 0) && (
          <div
            className={`pointer-events-none absolute inset-x-0 ${
              isFullscreen ? 'bottom-5' : 'bottom-4'
            } z-20 flex justify-center`}
          >
            <button
              type="button"
              onClick={handleResumeAutoScroll}
              className="pointer-events-auto eh-popover-in flex items-center gap-1.5 px-3.5 py-1.5 rounded-full ios-liquid-glass text-xs font-semibold text-white hover:scale-105 active:scale-95 transition-transform"
              style={{
                borderColor: 'rgba(var(--dynamic-accent-rgb), 0.55)',
                boxShadow:
                  '0 8px 24px rgba(0,0,0,0.65), 0 0 18px rgba(var(--dynamic-accent-rgb), 0.22)',
              }}
            >
              <ArrowDown size={13} style={{ color: 'var(--dynamic-accent)' }} />
              <span>Auto scroll</span>
            </button>
          </div>
        )}
      </div>
    </>
  );
};
