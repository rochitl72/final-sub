import { useState } from 'react';
import { ArrowRight, Check, Search } from 'lucide-react';
import type { Chip } from '../lib/api';
import { cx } from '../lib/format';

const OTHER_ID = 'clarify:other';
const NONE_ID = 'clarify:none';

/**
 * Vertical MCQ. `single` = radio (one tap answers), `multi` = checkboxes + Proceed.
 * "Something else" (clarify:other) opens a free-text box.
 */
export function MultiSelect({ options, mode = 'multi', allowOther = true, disabled = false, onSubmit }: {
  options: Chip[];
  mode?: 'single' | 'multi';
  allowOther?: boolean;
  /** Superseded question — show it, but it can no longer be answered. */
  disabled?: boolean;
  onSubmit: (ids: string[], otherText?: string) => void;
}) {
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [other, setOther] = useState('');
  const [sent, setSent] = useState(false);
  const done = sent || disabled;
  const setDone = setSent;
  const [query, setQuery] = useState('');
  const single = mode === 'single';
  const otherOn = !!selected[OTHER_ID];
  // Long lists (states, cities, offences) get a filter box; picked / special options always stay visible.
  const searchable = options.length > 8 && !done;
  const q = query.trim().toLowerCase();
  // Once answered (or superseded), a long list collapses to just what was picked.
  const collapsed = done && options.length > 8;
  const visible = collapsed
    ? options.filter((o) => selected[o.id])
    : q
      ? options.filter((o) => selected[o.id] || o.id.startsWith('clarify:') || o.label.toLowerCase().includes(q))
      : options;
  const hiddenCount = collapsed ? options.length - visible.length : 0;

  const toggle = (id: string) => {
    if (done) return;
    if (single) {
      setSelected({ [id]: true });
      if (id !== OTHER_ID) { setDone(true); onSubmit([id]); }
      return;
    }
    if (id === NONE_ID) { setSelected({ [NONE_ID]: !selected[NONE_ID] }); return; }
    setSelected((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      delete next[NONE_ID];
      return next;
    });
  };

  const chosen = options.filter((o) => selected[o.id]).map((o) => o.id);
  const canProceed = !done && chosen.length > 0;
  const proceed = () => {
    if (!canProceed) return;
    setDone(true);
    onSubmit(chosen, otherOn && other.trim() ? other.trim() : undefined);
  };

  return (
    <div className="space-y-2" role={single ? 'radiogroup' : 'group'}>
      {searchable && (
        <div className="relative">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-mist-dim" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && single) {
                const hits = visible.filter((o) => !o.id.startsWith('clarify:'));
                if (hits.length === 1) { e.preventDefault(); toggle(hits[0].id); }
              }
            }}
            placeholder={`Search ${options.length} options`}
            aria-label="Filter options"
            className="w-full rounded-xl border border-navy-border bg-navy-mid py-2.5 pl-9 pr-3 text-sm text-white placeholder:text-mist-dim focus:border-brand focus:outline-none"
          />
        </div>
      )}
      {searchable && q && visible.length === 0 && <div className="px-1 text-sm text-mist-soft">No match for “{query}”.</div>}
      {visible.map((opt) => {
        const on = !!selected[opt.id];
        return (
          <div key={opt.id}>
            <button
              type="button"
              disabled={done}
              onClick={() => toggle(opt.id)}
              role={single ? 'radio' : 'checkbox'}
              aria-checked={on}
              className={cx(
                'flex w-full items-start gap-3 rounded-xl border px-3.5 py-2.5 text-left text-sm transition',
                on ? 'border-brand bg-brand/10 font-semibold text-white' : 'border-navy-border bg-navy-light text-mist hover:border-brand-light/60 hover:text-white',
                done && 'cursor-default opacity-60 hover:border-navy-border',
              )}
            >
              <span className={cx('mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center border-[1.5px] transition', single ? 'rounded-full' : 'rounded-md', on ? 'border-brand bg-brand' : 'border-mist-soft')}>
                {on && <Check size={13} strokeWidth={3} className="text-white" />}
              </span>
              <span className="leading-snug">{opt.label}</span>
            </button>
            {opt.id === OTHER_ID && on && allowOther && (
              <textarea
                autoFocus
                value={other}
                disabled={done}
                onChange={(e) => setOther(e.target.value)}
                maxLength={500}
                rows={3}
                placeholder="Add your details here…"
                className="ml-8 mt-2 w-[calc(100%-2rem)] resize-none rounded-xl border border-navy-border bg-navy-mid p-2.5 text-sm text-white placeholder:text-mist-dim focus:border-brand focus:outline-none"
              />
            )}
          </div>
        );
      })}
      {hiddenCount > 0 && <div className="px-1 text-xs text-mist-dim">{visible.length ? `+ ${hiddenCount} other options` : `${hiddenCount} options`}</div>}
      {(!disabled || sent) && <div className="flex items-center justify-end gap-3 pt-1">
        {!done && !canProceed && <span className="text-xs italic text-mist-soft">{single ? 'Tap an option to answer' : 'Select at least one option'}</span>}
        {(!single || otherOn) && (
          <button
            type="button"
            onClick={proceed}
            disabled={!canProceed}
            className={cx('inline-flex items-center gap-1.5 rounded-full px-4 py-2 text-sm font-bold text-white transition', done ? 'bg-navy-mid' : 'bg-brand hover:bg-brand-light disabled:opacity-40')}
          >
            {done ? 'Sent' : 'Proceed'}
            {!done && canProceed && <ArrowRight size={14} />}
          </button>
        )}
      </div>}
    </div>
  );
}
