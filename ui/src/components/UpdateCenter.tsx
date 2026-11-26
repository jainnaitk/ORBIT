import type { FC } from 'react';
import {
  Download,
  RefreshCw,
  CheckCircle2,
  Sparkles,
  X,
  AlertCircle,
  Loader2,
  Rocket,
} from 'lucide-react';
import type { UpdateState } from '../api/types';

function formatBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return '';
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

/**
 * Extract up to `maxItems` brief, human-readable summary lines from GitHub Release markdown.
 */
function extractBriefChangelog(notes?: string | null, maxItems = 4): string[] {
  if (!notes) return [];
  const trimmed = notes.trim();
  if (!trimmed || /^no github releases published yet/i.test(trimmed)) return [];

  const items: string[] = [];
  for (const raw of trimmed.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (/^#{1,6}\s+/.test(line)) continue;
    if (/^[-*_]{3,}$/.test(line)) continue;

    const cleaned = line
      .replace(/^[-*+•]\s+/, '')
      .replace(/^\d+[.)]\s+/, '')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/[*_`~]/g, '')
      .replace(/\s+/g, ' ')
      .trim();

    if (cleaned.length < 4) continue;
    if (/^(full changelog|what'?s changed|sha256|checksum|https?:\/\/)/i.test(cleaned)) continue;
    if (/github\.com\/[^/\s]+\/[^/\s]+\/compare\//i.test(cleaned)) continue;

    items.push(cleaned.length > 96 ? `${cleaned.slice(0, 93).trimEnd()}...` : cleaned);
    if (items.length >= maxItems) break;
  }
  return items;
}

function resolveChangelogItems(updateState: UpdateState | null, maxItems = 4): string[] {
  if (!updateState) return [];
  if (Array.isArray(updateState.changelog) && updateState.changelog.length > 0) {
    const fromArray = extractBriefChangelog(updateState.changelog.join('\n'), maxItems);
    if (fromArray.length > 0) return fromArray;
  }
  return extractBriefChangelog(updateState.release_notes, maxItems);
}

interface UpdateAvailableModalProps {
  isOpen: boolean;
  updateState: UpdateState | null;
  onDismiss: () => void;
  onOpenDownloadTab: () => void;
}

export const UpdateAvailableModal: FC<UpdateAvailableModalProps> = ({
  isOpen,
  updateState,
  onDismiss,
  onOpenDownloadTab,
}) => {
  if (!isOpen || !updateState || !updateState.update_available) return null;

  const sizeLabel = formatBytes(updateState.asset_size);
  const changelogItems = resolveChangelogItems(updateState, 4);

  return (
    <div
      onClick={onDismiss}
      className="fixed inset-0 bg-black/70 eh-modal-backdrop z-[95] flex items-center justify-center p-4 select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md ios-liquid-popover eh-modal-in rounded-[26px] p-6 space-y-4"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-2xl flex items-center justify-center border border-white/15 flex-none"
              style={{ background: 'rgba(var(--dynamic-accent-rgb), 0.16)' }}
            >
              <Sparkles size={18} style={{ color: 'var(--dynamic-accent)' }} />
            </div>
            <div>
              <div
                className="text-[10px] font-mono uppercase tracking-[0.2em] font-semibold"
                style={{ color: 'var(--dynamic-accent)' }}
              >
                New Version Available
              </div>
              <h2 className="text-base font-bold text-[#F4F6FF] mt-0.5">
                {updateState.release_name || `ORBIT v${updateState.latest_version}`}
              </h2>
            </div>
          </div>

          <button
            type="button"
            onClick={onDismiss}
            aria-label="Close update notice"
            className="p-1 text-[#9BA4C4] hover:text-white rounded-lg hover:bg-white/10 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex items-center gap-2 text-[11px] font-mono text-[#9AA5C4]">
          <span className="px-2.5 py-1 rounded-full bg-white/[0.06] border border-white/10">
            v{updateState.current_version}
          </span>
          <span>&rarr;</span>
          <span
            className="px-2.5 py-1 rounded-full border font-semibold"
            style={{
              color: 'var(--dynamic-accent)',
              borderColor: 'rgba(var(--dynamic-accent-rgb), 0.45)',
              background: 'rgba(var(--dynamic-accent-rgb), 0.12)',
            }}
          >
            v{updateState.latest_version}
          </span>
          {sizeLabel && <span className="ml-auto text-[#7A84A6]">{sizeLabel}</span>}
        </div>

        {changelogItems.length > 0 && (
          <div className="rounded-2xl bg-black/45 border border-white/[0.08] p-3.5 space-y-2">
            <div className="text-[10px] font-mono uppercase tracking-[0.16em] text-[#8E99BA]">
              What&apos;s New
            </div>
            <ul className="space-y-1.5">
              {changelogItems.map((entry, idx) => (
                <li
                  key={idx}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/[0.05] border border-white/[0.08] text-[11px] text-[#DCE3FC] leading-snug"
                >
                  <span
                    className="w-1.5 h-1.5 rounded-full flex-none"
                    style={{
                      background: 'var(--dynamic-accent)',
                      boxShadow: '0 0 8px var(--dynamic-accent)',
                    }}
                  />
                  <span className="min-w-0 break-words">{entry}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex items-center justify-end gap-2.5 pt-1">
          <button
            type="button"
            onClick={onDismiss}
            className="px-4 py-2 rounded-full text-xs font-medium text-[#9BA4C4] hover:bg-white/10 hover:text-white transition-colors"
          >
            Later
          </button>
          <button
            type="button"
            onClick={onOpenDownloadTab}
            className="px-5 py-2 rounded-full text-xs font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90 active:scale-95 transition-all inline-flex items-center gap-1.5"
          >
            <Download size={13} />
            <span>Download</span>
          </button>
        </div>
      </div>
    </div>
  );
};

interface UpdateTabPopoverProps {
  isOpen: boolean;
  isConnected?: boolean;
  updateState: UpdateState | null;
  onClose: () => void;
  onCheckUpdates: () => void;
  onDownloadAndInstall: () => void;
}

export const UpdateTabPopover: FC<UpdateTabPopoverProps> = ({
  isOpen,
  isConnected = true,
  updateState,
  onClose,
  onCheckUpdates,
  onDownloadAndInstall,
}) => {
  if (!isOpen) return null;

  const fallbackVersion = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '1.2.6';
  const status = updateState?.status ?? 'idle';
  const currentVersion = updateState?.current_version ?? fallbackVersion;
  const latestVersion = updateState?.latest_version ?? currentVersion;
  const repo = updateState?.repo ?? 'jainnaitk/ORBIT';
  const isChecking = status === 'checking';
  const isDownloading = status === 'downloading';
  const isReady = status === 'ready';
  const isLaunched = status === 'launched';
  const isError = status === 'error';
  const hasUpdate = Boolean(updateState?.update_available);
  const hasKnownLatest =
    Boolean(updateState?.latest_version) &&
    !(isError && !hasUpdate && latestVersion === currentVersion);
  const pct = Math.max(0, Math.min(100, updateState?.progress_pct ?? 0));
  const downloadedLabel = formatBytes(updateState?.downloaded_bytes);
  const totalLabel = formatBytes(updateState?.total_bytes || updateState?.asset_size);
  const briefChangelog = resolveChangelogItems(updateState, 4);

  return (
    <>
      <div className="fixed inset-0 z-[85]" onClick={onClose} />
      <div
        onClick={(e) => e.stopPropagation()}
        className="absolute top-10 right-0 w-[310px] ios-liquid-popover eh-popover-in rounded-2xl p-3.5 z-[90] space-y-3 select-none"
      >
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-bold text-[#F4F6FF]">ORBIT Updates</span>
              {hasUpdate && (
                <span
                  className="text-[9px] font-mono uppercase px-1.5 py-0.5 rounded-full font-semibold"
                  style={{
                    color: 'var(--dynamic-accent)',
                    background: 'rgba(var(--dynamic-accent-rgb), 0.16)',
                  }}
                >
                  New
                </span>
              )}
            </div>
            <div className="text-[10px] font-mono text-[#7A84A6] truncate">
              github.com/{repo}
            </div>
          </div>

          <div className="flex items-center gap-1 flex-none">
            <button
              type="button"
              onClick={onCheckUpdates}
              disabled={isChecking || isDownloading}
              title="Check GitHub for updates"
              className="w-7 h-7 rounded-full flex items-center justify-center text-[#9AA5C4] hover:text-white hover:bg-white/10 disabled:opacity-40 transition-colors"
            >
              <RefreshCw size={13} className={isChecking ? 'animate-spin' : ''} />
            </button>
            <button
              type="button"
              onClick={onClose}
              title="Close"
              className="w-7 h-7 rounded-full flex items-center justify-center text-[#9AA5C4] hover:text-white hover:bg-white/10 transition-colors"
            >
              <X size={13} />
            </button>
          </div>
        </div>

        <div className="rounded-xl bg-black/45 border border-white/[0.08] p-3 space-y-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-[#9AA5C4]">Installed</span>
            <span className="font-mono text-[#F4F6FF]">v{currentVersion}</span>
          </div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-[#9AA5C4]">Latest on GitHub</span>
            <span
              className="font-mono font-semibold"
              style={
                hasUpdate
                  ? { color: 'var(--dynamic-accent)' }
                  : { color: hasKnownLatest || isChecking ? '#F4F6FF' : '#7A84A6' }
              }
            >
              {isChecking ? 'Checking...' : hasKnownLatest ? `v${latestVersion}` : 'Unavailable'}
            </span>
          </div>

          {isDownloading && (
            <div className="space-y-1.5 pt-1 border-t border-white/[0.07]">
              <div className="flex items-center justify-between text-[10px] font-mono text-[#A6B0CF]">
                <span className="truncate pr-2">
                  {updateState?.asset_name || 'ORBIT-Setup.exe'}
                </span>
                <span className="tabular-nums flex-none" style={{ color: 'var(--dynamic-accent)' }}>
                  {Math.round(pct)}%
                </span>
              </div>
              <div className="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
                <div
                  className="w-full h-full rounded-full origin-left will-change-transform transition-transform duration-150"
                  style={{
                    transform: `scaleX(${(pct / 100).toFixed(4)})`,
                    background: 'linear-gradient(90deg, var(--dynamic-secondary), var(--dynamic-accent))',
                  }}
                />
              </div>
              {(downloadedLabel || totalLabel) && (
                <div className="text-[10px] font-mono tabular-nums text-[#7A84A6] text-right">
                  {downloadedLabel || '0 KB'}
                  {totalLabel ? ` / ${totalLabel}` : ''}
                </div>
              )}
            </div>
          )}

          {status === 'error' && updateState?.error && (
            <div className="flex items-start gap-2 pt-1.5 border-t border-white/[0.07] text-[11px] text-[#FF6B9E]">
              <AlertCircle size={13} className="flex-none mt-0.5" />
              <span className="leading-snug break-words">{updateState.error}</span>
            </div>
          )}

          {isLaunched && (
            <div className="flex items-start gap-2 pt-1.5 border-t border-white/[0.07] text-[11px] text-[#34D399]">
              <Rocket size={13} className="flex-none mt-0.5" />
              <span className="leading-snug">
                Windows installer launched. ORBIT will update in place and relaunch.
              </span>
            </div>
          )}
        </div>

        {briefChangelog.length > 0 && (
          <div className="rounded-xl bg-white/[0.03] border border-white/[0.07] px-3 py-2.5 space-y-1.5">
            <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-[0.16em] text-[#8E99BA]">
              <span>What&apos;s New</span>
              <span style={{ color: 'var(--dynamic-accent)' }}>v{latestVersion}</span>
            </div>
            <ul className="space-y-1.5 text-[11px] text-[#DCE3FC] leading-snug">
              {briefChangelog.map((entry, idx) => (
                <li
                  key={idx}
                  className="flex items-center gap-2 px-2.5 py-1.5 rounded-full bg-white/[0.05] border border-white/[0.08]"
                >
                  <span
                    className="w-1.5 h-1.5 rounded-full flex-none"
                    style={{
                      background: 'var(--dynamic-accent)',
                      boxShadow: '0 0 8px var(--dynamic-accent)',
                    }}
                  />
                  <span className="min-w-0 break-words">{entry}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex items-center justify-between gap-2">
          {hasUpdate || isReady || isDownloading ? (
            <>
              <span className="text-[10px] text-[#9AA5C4] truncate">
                {updateState?.asset_name || `v${latestVersion} installer`}
                {totalLabel && !isDownloading ? ` • ${totalLabel}` : ''}
              </span>
              <button
                type="button"
                onClick={onDownloadAndInstall}
                disabled={isDownloading}
                className="px-3.5 py-1.5 rounded-full text-[11px] font-semibold bg-[#F4F6FF] text-[#020308] hover:opacity-90 active:scale-95 disabled:opacity-55 transition-all inline-flex items-center gap-1.5 flex-none"
              >
                {isDownloading ? (
                  <>
                    <Loader2 size={12} className="animate-spin" />
                    <span>{Math.round(pct)}%</span>
                  </>
                ) : isReady ? (
                  <>
                    <Rocket size={12} />
                    <span>Install</span>
                  </>
                ) : (
                  <>
                    <Download size={12} />
                    <span>{isError ? 'Retry' : 'Download'}</span>
                  </>
                )}
              </button>
            </>
          ) : (
            <div className="w-full flex items-center justify-between text-[11px]">
              {isChecking ? (
                <span className="inline-flex items-center gap-1.5 text-[#9AA5C4]">
                  <Loader2 size={13} className="animate-spin" />
                  <span>Checking GitHub...</span>
                </span>
              ) : isError ? (
                <span className="inline-flex items-center gap-1.5 text-[#FF6B9E]">
                  <AlertCircle size={13} />
                  <span>Update check failed</span>
                </span>
              ) : isLaunched ? (
                <span className="inline-flex items-center gap-1.5 text-[#34D399]">
                  <Rocket size={13} />
                  <span>Installer launched</span>
                </span>
              ) : !isConnected && !updateState ? (
                <span className="inline-flex items-center gap-1.5 text-[#9AA5C4]">
                  <Loader2 size={13} className="animate-spin" />
                  <span>Starting audio engine...</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-[#34D399]">
                  <CheckCircle2 size={13} />
                  <span>Up to date</span>
                </span>
              )}
              <button
                type="button"
                onClick={onCheckUpdates}
                disabled={isChecking}
                className="px-3 py-1 rounded-full text-[10px] font-medium ios-liquid-pill text-[#DCE3FC] hover:text-white disabled:opacity-45"
              >
                {isError ? 'Retry' : 'Check now'}
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
};
