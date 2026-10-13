import { PILL, type PillVariant } from '../hooks/useConnectivity';

/** Shows which engine answers in AI Chat. Tap to switch AI ↔ Rules only. */
export function EnginePill({ variant, onToggle }: { variant: PillVariant; onToggle: () => void }) {
  const m = PILL[variant];
  return (
    <button
      type="button"
      onClick={onToggle}
      title={`${m.hint}. Tap to switch AI / Rules only.`}
      aria-label={`Engine: ${m.label}. Tap to switch.`}
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10.5px] font-bold tracking-wide transition hover:brightness-125"
      style={{ color: m.dot, borderColor: m.dot + '55', background: m.dot + '14' }}
    >
      <span className="relative flex h-[7px] w-[7px]">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-50" style={{ background: m.dot }} />
        <span className="relative inline-flex h-[7px] w-[7px] rounded-full" style={{ background: m.dot }} />
      </span>
      {m.label}
    </button>
  );
}
