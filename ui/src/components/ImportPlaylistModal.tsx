import { useState, useEffect, useMemo, useRef, type FC, type FormEvent } from 'react';
import {
  X,
  Link2,
  ListPlus,
  Heart,
  FolderInput,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
} from 'lucide-react';
import { bridgeApi, bridgeSocket } from '../api/client';
import type {
  ImportDestination,
  ImportJobState,
  ImportPlatform,
  Playlist,
} from '../api/types';

interface ImportPlaylistModalProps {
  isOpen: boolean;
  playlists: Playlist[];
  onClose: () => void;
  onOpenPlaylist: (playlistId: string) => void;
  onOpenLiked: () => void;
  onImportComplete?: (job: ImportJobState) => void;
}

interface DetectedPlatform {
  platform: ImportPlatform;
  label: string;
  accentClass: string;
}

function detectPlatformBadge(rawUrl: string): DetectedPlatform | null {
  const trimmed = rawUrl.trim();
  if (!trimmed) return null;
  let candidate = trimmed;
  if (!/^[a-zA-Z][a-zA-Z\d+\-.]*:\/\//.test(candidate)) {
    candidate = `https://${candidate}`;
  }
  try {
    const parsed = new URL(candidate);
    const host = parsed.hostname.toLowerCase().replace(/\.$/, '');
    const path = parsed.pathname || '/';
    if (host === 'music.youtube.com') {
      if (parsed.searchParams.get('list') || /^\/browse\/[^/?#]+/.test(path)) {
        return {
          platform: 'youtube_music',
          label: 'YouTube Music',
          accentClass: 'bg-[#FF4D8D]/15 text-[#FF8FB8] border-[#FF4D8D]/35',
        };
      }
      return null;
    }
    if (
      host === 'youtube.com' ||
      host === 'www.youtube.com' ||
      host === 'm.youtube.com' ||
      host === 'youtu.be'
    ) {
      if (parsed.searchParams.get('list') || (host === 'youtu.be' && path.length > 1)) {
        return {
          platform: 'youtube',
          label: 'YouTube',
          accentClass: 'bg-[#F87171]/15 text-[#FCA5A5] border-[#F87171]/35',
        };
      }
      return null;
    }
    if (host === 'open.spotify.com') {
      if (/^\/(?:intl-[^/]+\/)?(playlist|album)\/[A-Za-z0-9]+/.test(path)) {
        return {
          platform: 'spotify',
          label: 'Spotify',
          accentClass: 'bg-[#34D399]/15 text-[#6EE7B7] border-[#34D399]/35',
        };
      }
      return null;
    }
    if (host === 'music.apple.com') {
      if (/\/(playlist|album)\/[^/]+/.test(path)) {
        return {
          platform: 'apple_music',
          label: 'Apple Music',
          accentClass: 'bg-[#A78BFA]/15 text-[#C4B5FD] border-[#A78BFA]/35',
        };
      }
      return null;
    }
  } catch {
    return null;
  }
  return null;
}

const ACTIVE_STATUSES = new Set(['queued', 'fetching', 'matching', 'saving']);

export const ImportPlaylistModal: FC<ImportPlaylistModalProps> = ({
  isOpen,
  playlists,
  onClose,
  onOpenPlaylist,
  onOpenLiked,
  onImportComplete,
}) => {
  const [url, setUrl] = useState('');
  const [destination, setDestination] = useState<ImportDestination>('new_playlist');
  const [playlistName, setPlaylistName] = useState('');
  const [targetPlaylistId, setTargetPlaylistId] = useState('');
  const [job, setJob] = useState<ImportJobState | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [showUnmatched, setShowUnmatched] = useState(false);
  const completedNotifiedJobRef = useRef<string | null>(null);

  const detected = useMemo(() => detectPlatformBadge(url), [url]);

  useEffect(() => {
    if (!isOpen) return;
    setSubmitError(null);
    if (!targetPlaylistId && playlists.length > 0) {
      setTargetPlaylistId(playlists[0].id);
    }
  }, [isOpen, playlists, targetPlaylistId]);

  useEffect(() => {
    if (!isOpen || !job?.job_id) return;
    const jobId = job.job_id;
    let cancelled = false;

    const handleUpdate = (incoming: ImportJobState) => {
      if (cancelled || incoming.job_id !== jobId) return;
      setJob(incoming);
      if (
        incoming.status === 'completed' &&
        completedNotifiedJobRef.current !== incoming.job_id
      ) {
        completedNotifiedJobRef.current = incoming.job_id;
        onImportComplete?.(incoming);
      }
    };

    const unsub = bridgeSocket.on('import_progress', handleUpdate);

    const pollTimer = window.setInterval(() => {
      if (!ACTIVE_STATUSES.has(job.status)) return;
      void bridgeApi
        .getImportJob(jobId)
        .then(handleUpdate)
        .catch(() => {});
    }, 750);

    return () => {
      cancelled = true;
      unsub();
      window.clearInterval(pollTimer);
    };
  }, [isOpen, job?.job_id, job?.status, onImportComplete]);

  if (!isOpen) return null;

  const isRunning = Boolean(job && ACTIVE_STATUSES.has(job.status));
  const isFinished = job?.status === 'completed';
  const isFailedOrCancelled = job?.status === 'error' || job?.status === 'cancelled';

  const progressRatio =
    job && job.total > 0
      ? Math.min(1, Math.max(0.03, job.processed / job.total))
      : job?.status === 'saving'
        ? 0.96
        : job?.status === 'fetching'
          ? 0.14
          : 0.05;

  const handleReset = () => {
    setJob(null);
    setSubmitError(null);
    setIsCancelling(false);
    setShowUnmatched(false);
  };

  const handleCloseSafe = () => {
    if (!isRunning) {
      handleReset();
      setUrl('');
      setPlaylistName('');
    }
    onClose();
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (isSubmitting || isRunning) return;
    const trimmed = url.trim();
    if (!trimmed) {
      setSubmitError('Paste a playlist or album link first.');
      return;
    }
    if (!detected) {
      setSubmitError(
        'Enter a valid YouTube, YouTube Music, Spotify, or Apple Music playlist or album URL.'
      );
      return;
    }
    if (destination === 'existing_playlist' && !targetPlaylistId) {
      setSubmitError('Select an existing playlist first.');
      return;
    }

    setSubmitError(null);
    setShowUnmatched(false);
    setIsCancelling(false);
    setIsSubmitting(true);
    try {
      const started = await bridgeApi.importFromUrl({
        url: trimmed,
        destination,
        playlist_name: destination === 'new_playlist' ? playlistName.trim() || null : null,
        target_playlist_id: destination === 'existing_playlist' ? targetPlaylistId : null,
      });
      setJob(started);
    } catch (err: any) {
      setSubmitError(err?.message || 'Unable to start playlist import.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancelJob = async () => {
    if (!job || isCancelling) return;
    setIsCancelling(true);
    try {
      const updated = await bridgeApi.cancelImportJob(job.job_id);
      setJob(updated);
    } catch {
      // Handled gracefully
    } finally {
      setIsCancelling(false);
    }
  };

  const handleOpenDestination = () => {
    if (!job) return;
    const dest = job.destination;
    const pid = job.target_playlist_id;
    handleReset();
    setUrl('');
    setPlaylistName('');
    onClose();
    if (dest === 'liked') {
      onOpenLiked();
    } else if (pid) {
      onOpenPlaylist(pid);
    }
  };

  return (
    <div
      onClick={handleCloseSafe}
      className="fixed inset-0 bg-black/75 eh-modal-backdrop z-50 flex items-center justify-center p-4 select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg ios-liquid-popover eh-modal-in rounded-[26px] p-6 space-y-5"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className="w-9 h-9 rounded-2xl border border-white/15 flex items-center justify-center flex-none"
              style={{ background: 'rgba(var(--dynamic-accent-rgb), 0.16)' }}
            >
              <FolderInput size={17} style={{ color: 'var(--dynamic-accent)' }} />
            </div>
            <div>
              <h2 className="text-base font-bold text-[#F4F6FF]">Import Playlist</h2>
              <p className="text-[11px] text-[#9BA4C4]">
                YouTube, YouTube Music, Spotify &amp; Apple Music links
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleCloseSafe}
            aria-label="Close import modal"
            className="p-1.5 text-[#9BA4C4] hover:text-white rounded-lg hover:bg-white/10 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {!job ? (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8E99BA]">
                  Playlist or Album URL
                </label>
                {detected && (
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold border ${detected.accentClass}`}
                  >
                    {detected.label}
                  </span>
                )}
              </div>
              <div className="relative">
                <Link2
                  size={14}
                  className="absolute left-3.5 top-1/2 -translate-y-1/2 text-[#7E89AB] pointer-events-none"
                />
                <input
                  type="url"
                  value={url}
                  onChange={(e) => {
                    setUrl(e.target.value);
                    if (submitError) setSubmitError(null);
                  }}
                  placeholder="https://open.spotify.com/playlist/... or music.apple.com/..."
                  autoFocus
                  className="w-full h-10 pl-9 pr-3.5 rounded-xl bg-white/[0.06] border border-white/15 text-xs text-[#F4F6FF] placeholder-[#6E789B] focus:outline-none focus:border-white/35"
                />
              </div>
            </div>

            <div className="space-y-2">
              <span className="block text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8E99BA]">
                Destination
              </span>
              <div className="grid grid-cols-3 gap-2">
                {(
                  [
                    { id: 'new_playlist', label: 'New Playlist', icon: ListPlus },
                    { id: 'existing_playlist', label: 'Existing Playlist', icon: FolderInput },
                    { id: 'liked', label: 'Liked Songs', icon: Heart },
                  ] as const
                ).map((opt) => {
                  const Icon = opt.icon;
                  const active = destination === opt.id;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => setDestination(opt.id)}
                      className={`flex items-center justify-center gap-1.5 py-2.5 px-2.5 rounded-xl text-[11px] font-semibold border transition-colors ${
                        active
                          ? 'bg-[#F4F6FF] text-[#020308] border-[#F4F6FF]'
                          : 'bg-white/[0.05] text-[#B8C2E0] border-white/10 hover:bg-white/[0.1] hover:text-white'
                      }`}
                    >
                      <Icon size={13} />
                      <span>{opt.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {destination === 'new_playlist' && (
              <div className="space-y-1.5">
                <label className="block text-[11px] text-[#9BA4C4]">
                  Playlist Name <span className="text-[#6E789B]">(optional override)</span>
                </label>
                <input
                  type="text"
                  value={playlistName}
                  onChange={(e) => setPlaylistName(e.target.value)}
                  placeholder="Leave blank to keep source playlist title"
                  className="w-full h-9 px-3.5 rounded-xl bg-white/[0.05] border border-white/12 text-xs text-[#F4F6FF] placeholder-[#6E789B] focus:outline-none focus:border-white/30"
                />
              </div>
            )}

            {destination === 'existing_playlist' && (
              <div className="space-y-1.5">
                <label className="block text-[11px] text-[#9BA4C4]">Choose Playlist</label>
                {playlists.length === 0 ? (
                  <div className="px-3.5 py-2.5 rounded-xl bg-white/[0.04] border border-white/10 text-xs text-[#9BA4C4]">
                    No playlists created yet. Switch to New Playlist or Liked Songs.
                  </div>
                ) : (
                  <select
                    value={targetPlaylistId}
                    onChange={(e) => setTargetPlaylistId(e.target.value)}
                    className="w-full h-9 px-3 rounded-xl bg-[#0D111C] border border-white/15 text-xs text-[#F4F6FF] focus:outline-none focus:border-white/35"
                  >
                    {playlists.map((pl) => (
                      <option key={pl.id} value={pl.id}>
                        {pl.name} ({pl.track_count} tracks)
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}

            {submitError && (
              <div className="flex items-start gap-2 p-3 rounded-xl bg-[#FF4D8D]/10 border border-[#FF4D8D]/30 text-xs text-[#FFB3CE]">
                <AlertCircle size={14} className="mt-0.5 flex-none text-[#FF4D8D]" />
                <span>{submitError}</span>
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={handleCloseSafe}
                className="px-4 py-2 rounded-full text-xs text-[#9BA4C4] hover:text-white hover:bg-white/[0.06] transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={
                  isSubmitting ||
                  !url.trim() ||
                  (destination === 'existing_playlist' && playlists.length === 0)
                }
                className="inline-flex items-center gap-1.5 px-5 py-2 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90 disabled:opacity-40 transition-opacity"
              >
                {isSubmitting && <Loader2 size={13} className="animate-spin" />}
                <span>Start Import</span>
              </button>
            </div>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="p-4 rounded-2xl bg-white/[0.04] border border-white/10 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-[#F4F6FF] truncate">
                    {job.playlist_name}
                  </div>
                  <div className="text-[11px] text-[#9BA4C4] mt-0.5">
                    {job.status === 'fetching' && 'Reading playlist tracks...'}
                    {job.status === 'queued' && 'Queued...'}
                    {job.status === 'matching' &&
                      (job.total > 0
                        ? `Matched ${job.matched} / ${job.total}... (${job.processed} checked)`
                        : 'Matching tracks...')}
                    {job.status === 'saving' && `Saving ${job.matched} matched tracks...`}
                    {job.status === 'completed' &&
                      `${job.matched} / ${job.total} tracks imported`}
                    {job.status === 'cancelled' &&
                      `Import cancelled (${job.processed} / ${job.total} processed)`}
                    {job.status === 'error' && (job.error || 'Import failed')}
                  </div>
                </div>
                {isRunning && <Loader2 size={16} className="animate-spin text-[#B8C2E0] flex-none" />}
                {isFinished && <CheckCircle2 size={18} className="text-[#34D399] flex-none" />}
                {isFailedOrCancelled && (
                  <AlertCircle size={18} className="text-[#FF4D8D] flex-none" />
                )}
              </div>

              <div className="h-1.5 w-full rounded-full bg-white/[0.08] overflow-hidden">
                <div
                  className="h-full w-full rounded-full origin-left transition-transform duration-150 ease-out"
                  style={{
                    transform: `scaleX(${isFinished ? 1 : progressRatio})`,
                    backgroundColor: isFailedOrCancelled
                      ? '#FF4D8D'
                      : isFinished
                        ? '#34D399'
                        : 'var(--dynamic-accent)',
                  }}
                />
              </div>
            </div>

            {job.unmatched_items.length > 0 && (
              <div className="rounded-xl bg-white/[0.03] border border-white/10 overflow-hidden">
                <button
                  type="button"
                  onClick={() => setShowUnmatched((prev) => !prev)}
                  className="w-full flex items-center justify-between px-3.5 py-2.5 text-xs text-[#C6CFE8] hover:bg-white/[0.04] transition-colors"
                >
                  <span>
                    Unmatched tracks ({job.unmatched_items.length})
                  </span>
                  {showUnmatched ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </button>
                {showUnmatched && (
                  <div className="max-h-36 overflow-y-auto px-3.5 pb-3 space-y-1 text-[11px] text-[#9BA4C4] border-t border-white/[0.06] pt-2">
                    {job.unmatched_items.map((item, idx) => (
                      <div key={`${item}-${idx}`} className="truncate">
                        • {item}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-1">
              {isRunning ? (
                <>
                  <button
                    type="button"
                    onClick={handleCancelJob}
                    disabled={isCancelling || job.status === 'saving'}
                    className="px-4 py-2 rounded-full text-xs font-medium bg-[#FF4D8D]/15 text-[#FFB3CE] border border-[#FF4D8D]/30 hover:bg-[#FF4D8D]/25 transition-colors disabled:opacity-50"
                  >
                    {isCancelling ? 'Cancelling...' : 'Cancel Import'}
                  </button>
                  <button
                    type="button"
                    onClick={handleCloseSafe}
                    className="px-4 py-2 rounded-full text-xs text-[#9BA4C4] hover:text-white hover:bg-white/[0.06] transition-colors"
                  >
                    Hide
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={handleReset}
                    className="px-4 py-2 rounded-full text-xs text-[#B8C2E0] hover:text-white hover:bg-white/[0.08] transition-colors"
                  >
                    Import Another
                  </button>
                  {isFinished ? (
                    <button
                      type="button"
                      onClick={handleOpenDestination}
                      className="inline-flex items-center gap-1.5 px-5 py-2 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90 transition-opacity"
                    >
                      <span>
                        {job.destination === 'liked' ? 'Open Liked Songs' : 'Open Playlist'}
                      </span>
                      <ExternalLink size={12} />
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={handleCloseSafe}
                      className="px-5 py-2 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90 transition-opacity"
                    >
                      Done
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
