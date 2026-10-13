import { create } from 'zustand';
import { Info } from 'lucide-react';

interface ToastState { msg: string; key: number; show: (msg: string) => void }

let timer: ReturnType<typeof setTimeout> | null = null;

export const useToast = create<ToastState>((set) => ({
  msg: '',
  key: 0,
  show: (msg) => {
    if (timer) clearTimeout(timer);
    set((s) => ({ msg, key: s.key + 1 }));
    timer = setTimeout(() => set({ msg: '' }), 3200);
  },
}));

export const toast = (msg: string) => useToast.getState().show(msg);

/** One toast at a time, floating above the composer. */
export function ToastHost() {
  const { msg, key } = useToast();
  if (!msg) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-28 z-[60] flex justify-center px-4" aria-live="polite">
      <div key={key} className="glass flex max-w-md animate-rise items-center gap-2 rounded-full border border-brand/50 px-5 py-2.5 text-sm font-semibold text-white shadow-glow">
        <Info size={15} className="shrink-0 text-brand-pale" />
        <span>{msg}</span>
      </div>
    </div>
  );
}
