/**
 * HomeScreen — chat list + new chat. Full screen on phones; the left sidebar on desktop.
 */
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Calculator, CloudDownload, Plus, RefreshCw, Sparkles } from 'lucide-react';
import type { SessionMode } from '../lib/api';
import { healthApi } from '../lib/api';
import { shouldShowBanner } from '../lib/updates';
import { cx } from '../lib/format';
import { useAuthStore } from '../store/auth';
import { useChatStore } from '../store/chat';
import { useSessions } from '../hooks/useSessions';
import { useDesktop } from '../hooks/useDesktop';
import { Logo } from '../components/Brand';
import { SessionCard } from '../components/SessionCard';
import { UpdateBanner } from '../components/UpdateBanner';
import { toast } from '../components/Toast';

function ModeSwitch({ value, onChange }: { value: SessionMode; onChange: (m: SessionMode) => void }) {
  return (
    <div className="relative mx-4 mb-4 grid grid-cols-2 rounded-2xl border border-navy-border bg-navy-mid p-[3px]" role="tablist" aria-label="Chat mode">
      <span className={cx('absolute inset-y-[3px] w-[calc(50%-3px)] rounded-xl bg-glow shadow-glow transition-all duration-300', value === 'static' ? 'left-[3px]' : 'left-1/2')} />
      {([['static', 'Calculator', Calculator], ['dynamic', 'AI Chat', Sparkles]] as const).map(([m, label, Icon]) => (
        <button key={m} type="button" role="tab" aria-selected={value === m} onClick={() => onChange(m)}
          className={cx('relative z-10 flex items-center justify-center gap-1.5 py-2.5 text-sm font-semibold transition', value === m ? 'text-white' : 'text-mist-soft hover:text-white')}>
          <Icon size={15} /> {label}
        </button>
      ))}
    </div>
  );
}

function HealthDot() {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => { healthApi.check().then((h) => setOk(h.ready)); }, []);
  const color = ok === null ? '#8899bb' : ok ? '#10b981' : '#f59e0b';
  return (
    <span className="relative flex h-2 w-2" title={ok ? 'Server connected' : ok === false ? 'Server unreachable — working on this device' : 'Checking server'}>
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-50" style={{ background: color }} />
      <span className="relative h-2 w-2 rounded-full" style={{ background: color }} />
    </span>
  );
}

export default function HomeScreen() {
  const desktop = useDesktop();
  const [params] = useSearchParams();
  const activeId = params.get('sessionId');
  const user = useAuthStore((s) => s.user);
  const refreshAuth = useAuthStore((s) => s.refresh);
  const sessions = useChatStore((s) => s.sessions);
  const { load, open, create, remove } = useSessions();

  const [mode, setMode] = useState<SessionMode>('static');
  const [banner, setBanner] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    load();
    shouldShowBanner().then(setBanner);
  }, [load]);

  const newChat = async () => {
    if (busy) return;
    setBusy(true);
    await create(mode);
    setBusy(false);
  };

  const refreshDevice = async () => {
    if (!window.confirm('Get a new access token for this device? Your chats stay.')) return;
    await refreshAuth();
    await load();
    toast('Session refreshed');
  };

  const initial = (user?.name?.[0] || user?.email?.[0] || 'G').toUpperCase();

  return (
    <div className="bg-deep relative flex h-full flex-col">
      <header className="safe-top flex items-center justify-between px-4 pb-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <Logo size={40} />
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[22px] font-extrabold tracking-tight text-white">DriveLegal <HealthDot /></div>
            <div className="truncate text-xs text-mist-soft">Indian road-law assistant</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setBanner(true)} title="Check for law updates"
            className="inline-flex items-center gap-1 rounded-xl border border-brand/35 bg-[linear-gradient(135deg,#1a3a5c,#1f4878)] px-2.5 py-1.5 text-xs font-semibold text-brand-light hover:brightness-125">
            <CloudDownload size={14} /> Update
          </button>
          <button type="button" onClick={refreshDevice} title={user ? `Guest device · tap to refresh session` : 'Refresh session'} aria-label="Refresh session"
            className="flex h-9 w-9 items-center justify-center rounded-full bg-gloss font-bold text-white shadow-glow">
            {initial}
          </button>
        </div>
      </header>

      <ModeSwitch value={mode} onChange={setMode} />

      {desktop && (
        <div className="px-4 pb-3">
          <button type="button" onClick={newChat} disabled={busy} className="btn-primary w-full py-3">
            {busy ? <RefreshCw size={17} className="animate-spin" /> : <Plus size={18} />}
            New {mode === 'static' ? 'calculation' : 'AI chat'}
          </button>
        </div>
      )}

      {banner && <UpdateBanner onClose={() => setBanner(false)} />}

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto pb-28 lg:pb-4">
        {sessions.length === 0 ? (
          <div className="flex h-full animate-rise flex-col items-center justify-center gap-3 p-10 text-center">
            <Logo size={80} glow />
            <div className="mt-2 text-2xl font-bold text-white">No chats yet</div>
            <p className="max-w-xs text-[15px] leading-relaxed text-mist-soft">Start a session to calculate a fine, or describe an incident to the AI.</p>
            {!desktop && (
              <button type="button" onClick={newChat} className="btn-primary mt-4">
                <Plus size={18} /> New chat
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="px-5 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-widest text-mist-dim">Recent</div>
            {sessions.map((s) => (
              <SessionCard key={s.id} session={s} active={s.id === activeId} onOpen={() => open(s)} onDelete={() => remove(s, activeId)} />
            ))}
          </>
        )}
      </div>

      {!desktop && sessions.length > 0 && (
        <button type="button" onClick={newChat} disabled={busy} aria-label="New chat"
          style={{ bottom: 'max(1.5rem, calc(env(safe-area-inset-bottom) + 0.75rem))' }}
          className="fixed right-6 flex h-16 w-16 items-center justify-center rounded-full bg-gloss text-white shadow-glow-lg transition active:scale-90">
          <Plus size={30} />
        </button>
      )}
    </div>
  );
}
