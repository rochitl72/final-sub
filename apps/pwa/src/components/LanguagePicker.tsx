import { useState } from 'react';
import { Check, Globe, WifiOff } from 'lucide-react';
import { SUPPORTED_LANGUAGES, type Language } from '../lib/sarvam';
import { usePrefs } from '../store/prefs';
import { cx } from '../lib/format';
import { Sheet } from './Sheet';

/** Header button + picker for the reply language (11 Indian languages, translated by Sarvam). */
export function LanguagePicker({ enabled, onChange }: { enabled: boolean; onChange: (l: Language) => void }) {
  const { language, setLanguage } = usePrefs();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => enabled && setOpen(true)}
        title={enabled ? `Reply language: ${language.label}` : 'Translation needs the server'}
        aria-label="Reply language"
        className={cx('icon-btn relative', !enabled && 'cursor-not-allowed opacity-50')}
        style={enabled ? { borderColor: language.color + '66', color: language.color, background: language.color + '1f' } : undefined}
      >
        <span className="text-[15px] font-bold leading-none">{language.script}</span>
        <Globe size={10} className="absolute bottom-1 right-1" />
      </button>

      <Sheet open={open} onClose={() => setOpen(false)} title="Reply language" icon={<Globe size={18} className="text-brand-pale" />}>
        <p className="mb-4 text-sm text-mist-soft">Answers are translated by Sarvam AI. Fine amounts and sections stay exactly the same.</p>
        <div className="grid grid-cols-2 gap-2 pb-2 sm:grid-cols-3">
          {SUPPORTED_LANGUAGES.map((l) => {
            const on = l.code === language.code;
            return (
              <button
                key={l.code}
                type="button"
                onClick={() => { setLanguage(l); setOpen(false); if (!on) onChange(l); }}
                className={cx('relative flex items-center gap-3 rounded-2xl border p-3 text-left transition hover:brightness-125', on ? 'border-2' : 'border-navy-border bg-navy-mid')}
                style={on ? { borderColor: l.color, background: l.color + '1a' } : undefined}
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-lg font-bold" style={{ color: l.color, background: l.color + '22' }}>{l.script}</span>
                <span className="min-w-0">
                  <span className="block truncate font-semibold text-white">{l.name}</span>
                  <span className="block text-xs text-mist-soft">{l.label}</span>
                </span>
                {on && <Check size={15} className="absolute right-2.5 top-2.5" style={{ color: l.color }} />}
              </button>
            );
          })}
        </div>
        {!enabled && <p className="flex items-center gap-2 pb-3 text-xs text-mist-soft"><WifiOff size={13} /> Needs the server</p>}
      </Sheet>
    </>
  );
}
