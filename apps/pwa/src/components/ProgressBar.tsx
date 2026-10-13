import { AlertCircle, Car, MapPin, Route } from 'lucide-react';
import type { SessionState } from '../lib/api';
import { cx } from '../lib/format';

const STEPS = [
  { key: 'location', label: 'Location', Icon: MapPin, field: 'state_code' },
  { key: 'road', label: 'Road', Icon: Route, field: 'road_bucket' },
  { key: 'vehicle', label: 'Vehicle', Icon: Car, field: 'vehicle_segment' },
  { key: 'violation', label: 'Violation', Icon: AlertCircle, field: 'violation_code' },
] as const;

/** Calculator progress: Location → Road → Vehicle → Violation. */
export function ProgressBar({ state }: { state: SessionState | null }) {
  if (!state) return null;
  const filled = STEPS.map((s) => !!state[s.field]);
  const pct = (filled.filter(Boolean).length / STEPS.length) * 100;
  return (
    <div className="border-b border-navy-border bg-navy-mid/80 px-4 pb-2.5 pt-2.5">
      <div className="mx-auto flex max-w-xl justify-around">
        {STEPS.map((s, i) => {
          const done = filled[i];
          const active = !done && (i === 0 || filled[i - 1]);
          return (
            <div key={s.key} className="flex flex-1 flex-col items-center gap-1">
              <span className={cx('flex h-8 w-8 items-center justify-center rounded-xl border transition',
                done ? 'border-transparent bg-gloss text-white' : active ? 'border-brand bg-navy-light text-brand-pale' : 'border-navy-border bg-navy-mid text-mist-dim')}>
                <s.Icon size={15} />
              </span>
              <span className={cx('text-[11px]', done ? 'font-semibold text-white' : active ? 'text-brand-pale' : 'text-mist-dim')}>{s.label}</span>
            </div>
          );
        })}
      </div>
      <div className="mx-auto mt-2 h-[3px] max-w-xl overflow-hidden rounded-full bg-navy-border">
        <div className="h-full rounded-full bg-glow transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
