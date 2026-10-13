import { useState } from 'react';
import { ExternalLink, ShieldAlert, ShieldCheck, Wrench } from 'lucide-react';
import type { Citation, VerifyStatus } from '../lib/api';
import { cx } from '../lib/format';

/**
 * Where an amount comes from — from the Sept-2026 dataset audit (docs/DATASET_VALIDATION_REPORT.md).
 *   verified   matched the Motor Vehicles Act text; links the source
 *   corrected  the old figure contradicted the Act; this is the statutory amount
 *   unverified no source could be found (e.g. state notifications) — shown, but flagged
 */
const META: Record<VerifyStatus, { label: string; short: string; Icon: typeof ShieldCheck; tone: string }> = {
  verified: { label: 'Verified against the MV Act', short: 'verified', Icon: ShieldCheck, tone: 'text-emerald-400 border-emerald-500/50 bg-emerald-500/10' },
  corrected: { label: 'Corrected to the statutory amount', short: 'corrected', Icon: Wrench, tone: 'text-accent border-accent/50 bg-accent/10' },
  unverified: { label: 'Amount not verified', short: 'unverified', Icon: ShieldAlert, tone: 'text-amber-400 border-amber-500/50 bg-amber-500/10' },
};

const host = (url: string) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return 'source'; } };

/** Full strip for the single-offence fine card. */
export function ProvenanceStrip({ status, note, citation }: { status?: VerifyStatus | null; note?: string | null; citation?: Citation | null }) {
  const [open, setOpen] = useState(status !== 'verified');
  if (!status) return null;
  const m = META[status];
  return (
    <div className="mx-4 border-t border-navy-border py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cx('inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold', m.tone)}>
          <m.Icon size={13} /> {m.label}
        </span>
        {citation?.url && (
          <a href={citation.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-brand-pale hover:text-white">
            Source: {host(citation.url)} <ExternalLink size={11} />
          </a>
        )}
        {note && status === 'verified' && (
          <button type="button" onClick={() => setOpen(!open)} className="text-xs text-mist-soft underline-offset-2 hover:text-white hover:underline">
            {open ? 'Hide note' : 'Note'}
          </button>
        )}
      </div>
      {note && open && <p className="mt-2 text-[13px] leading-relaxed text-mist-soft">{note}</p>}
    </div>
  );
}

/** Compact tag for one offence inside a person card. */
export function ProvenanceTag({ status, note, citation }: { status?: VerifyStatus | null; note?: string | null; citation?: Citation | null }) {
  if (!status) return null;
  const m = META[status];
  const tag = (
    <span title={note || m.label} className={cx('inline-flex items-center gap-1 rounded-full border px-2 py-px text-[10.5px] font-semibold', m.tone)}>
      <m.Icon size={10} /> {m.short}
    </span>
  );
  return citation?.url ? (
    <a href={citation.url} target="_blank" rel="noopener noreferrer" className="hover:brightness-125" aria-label={`${m.label} — open source`}>{tag}</a>
  ) : tag;
}
