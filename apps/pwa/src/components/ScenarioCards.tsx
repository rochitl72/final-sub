import { User } from 'lucide-react';
import type { Scenario } from '../lib/api';
import { cx, inr } from '../lib/format';
import { ProvenanceTag } from './Provenance';

function Tag({ tone, children }: { tone: 'warn' | 'info' | 'crim'; children: string }) {
  const c = tone === 'warn' ? 'border-amber-500/70 text-amber-400' : tone === 'info' ? 'border-brand-pale/70 text-brand-pale' : 'border-red-500/70 text-red-400';
  return <span className={cx('rounded-full border px-2 py-px text-[10.5px] font-semibold', c)}>{children}</span>;
}

/** One card per person in a multi-person / multi-offence story. */
export function ScenarioCards({ scenario }: { scenario: Scenario }) {
  const people = scenario.people ?? [];
  if (!people.length) return null;
  return (
    <div className="space-y-2.5">
      {people.map((p, i) => {
        const meta = [p.age != null ? `${p.age} yrs` : '', (p.roles ?? []).join(', ')].filter(Boolean).join(' · ');
        const offences = p.offences ?? [];
        return (
          <div
            key={p.id || i}
            className={cx('animate-rise rounded-2xl border bg-navy-light p-3.5', p.kind === 'victim' ? 'border-dashed border-navy-border opacity-85' : 'border-navy-border')}
            style={{ animationDelay: `${i * 60}ms` }}
          >
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/20 text-brand-pale"><User size={13} /></span>
                <span className="truncate text-sm font-bold text-white">{p.label || 'Person'}</span>
                {meta && <span className="truncate text-xs text-mist-soft">{meta}</span>}
              </div>
              {p.total_first && offences.length > 1 ? (
                <span className="shrink-0 text-[13px] font-bold text-brand-pale">Total {inr(p.total_first)}</span>
              ) : null}
            </div>
            {offences.length === 0 && <div className="py-1 text-xs text-mist-soft">No offence for this person.</div>}
            {offences.map((o, j) => {
              const sub = [o.section, o.imprisonment ? `jail: ${o.imprisonment}` : '', o.compoundable === false ? 'goes to court' : '', o.licence_action || '']
                .filter(Boolean).join(' · ');
              return (
                <div key={o.code + j} className={cx('flex justify-between gap-3 py-2', j > 0 && 'border-t border-white/[0.07]')}>
                  <div className="min-w-0">
                    <div className="text-[13.5px] text-white">{o.name}</div>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {o.certainty === 'may_be_liable' && <Tag tone="warn">may apply</Tag>}
                      {(o.deemed || o.juvenile) && <Tag tone="info">s.199A</Tag>}
                      {o.severity === 'criminal' && <Tag tone="crim">criminal</Tag>}
                      <ProvenanceTag status={o.verify_status} note={o.verify_note} citation={o.citation} />
                    </div>
                    {sub && <div className="mt-1 text-[11.5px] text-mist-soft">{sub}</div>}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="text-[13.5px] font-bold text-white">{o.fine_first ? inr(o.fine_first) : 'court'}</div>
                    {o.fine_repeat && o.fine_repeat !== o.fine_first ? <div className="text-[11.5px] text-mist-soft">repeat {inr(o.fine_repeat)}</div> : null}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
