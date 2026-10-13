import { Calculator, Sparkles, Trash2 } from 'lucide-react';
import type { Session } from '../lib/api';
import { cx, relativeTime } from '../lib/format';

export const MODE_META = {
  static: { label: 'Calculator', Icon: Calculator, color: '#2563eb' },
  dynamic: { label: 'AI Chat', Icon: Sparkles, color: '#06b6d4' },
} as const;

export function SessionCard({ session, active, onOpen, onDelete }: { session: Session; active: boolean; onOpen: () => void; onDelete: () => void }) {
  const meta = MODE_META[session.mode] ?? MODE_META.static;
  return (
    <div className="group relative mx-3 my-1">
      <button
        type="button"
        onClick={onOpen}
        aria-current={active ? 'page' : undefined}
        className={cx(
          'relative flex w-full items-center gap-3 overflow-hidden rounded-2xl border p-3 pr-10 text-left transition active:scale-[0.99]',
          active ? 'border-brand/60 bg-[linear-gradient(135deg,rgba(37,99,235,0.22),rgba(26,79,168,0.12))]' : 'border-navy-border bg-[linear-gradient(135deg,rgba(15,32,64,0.85),rgba(10,22,40,0.9))] hover:border-brand-light/40',
        )}
      >
        {active && <span className="absolute inset-y-[18%] left-0 w-[3px] rounded-full" style={{ background: meta.color }} />}
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border bg-brand/[0.08]" style={{ borderColor: meta.color + '55', color: meta.color }}>
          <meta.Icon size={20} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] font-semibold text-white">{session.title || 'New chat'}</span>
          {!!session.last_snippet && <span className="block truncate text-[13px] text-mist-soft">{session.last_snippet.replace(/\*\*/g, '')}</span>}
          <span className="mt-1 flex items-center gap-2">
            <span className="rounded-full border px-2 py-px text-[11px] font-medium" style={{ color: meta.color, borderColor: meta.color + '55' }}>{meta.label}</span>
            <span className="text-[11px] text-mist-dim">{relativeTime(session.updated_at)}</span>
          </span>
        </span>
      </button>
      <button
        type="button"
        onClick={onDelete}
        aria-label={`Delete ${session.title || 'chat'}`}
        className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-lg text-mist-dim transition hover:bg-red-500/10 hover:text-red-400 lg:opacity-0 lg:group-hover:opacity-100 lg:focus:opacity-100"
      >
        <Trash2 size={15} />
      </button>
    </div>
  );
}
