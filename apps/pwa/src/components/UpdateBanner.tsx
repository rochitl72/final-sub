import { useEffect, useState } from 'react';
import { CloudDownload, Loader2, RefreshCw, X } from 'lucide-react';
import { getUpdateStatus, remindLater, runUpdate } from '../lib/updates';

/** Law-dataset freshness prompt. Updates come only from .gov.in sources and are applied as patches. */
export function UpdateBanner({ onClose }: { onClose: () => void }) {
  const [days, setDays] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => { getUpdateStatus().then((s) => s && setDays(s.days_since_run)); }, []);

  const update = async () => {
    setRunning(true);
    const r = await runUpdate();
    setRunning(false);
    setResult(r?.ok ? 'Update started in the background' : 'Could not reach the server');
    setTimeout(onClose, 2200);
  };
  const remind = (d: number) => { remindLater(d); onClose(); };
  const ago = days === null ? 'never' : days === 0 ? 'today' : `${days}d ago`;

  return (
    <div className="relative mx-4 mb-3 animate-rise overflow-hidden rounded-2xl border border-brand/30 bg-[linear-gradient(135deg,#0d1f3c,#112240)] shadow-glow">
      <span className="absolute inset-y-0 left-0 w-[3px] bg-brand" />
      <div className="space-y-2.5 py-3 pl-5 pr-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-glow text-white"><CloudDownload size={17} /></span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-bold text-white">Law dataset update</div>
            <div className="text-xs text-mist-soft">Last checked {ago} · .gov.in sources only</div>
          </div>
          <button type="button" onClick={() => remind(1)} className="p-1 text-mist-soft hover:text-white" aria-label="Dismiss"><X size={17} /></button>
        </div>
        {result ? (
          <div className="text-sm font-semibold text-brand-light">{result}</div>
        ) : running ? (
          <div className="flex items-center gap-2 text-sm text-mist-soft"><Loader2 size={15} className="animate-spin" /> Contacting the server…</div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={update} className="inline-flex items-center gap-1.5 rounded-xl bg-gloss px-3.5 py-2 text-sm font-semibold text-white hover:brightness-110">
              <RefreshCw size={14} /> Update now
            </button>
            <span className="ml-1 text-xs text-mist-soft">Remind me in</span>
            {[7, 14, 30].map((d) => <button key={d} type="button" onClick={() => remind(d)} className="chip">{d}d</button>)}
          </div>
        )}
      </div>
    </div>
  );
}
