import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useDesktop } from '../hooks/useDesktop';
import { cx } from '../lib/format';

/**
 * Bottom sheet on phones, centred dialog on desktop. Esc / backdrop click closes it.
 */
export function Sheet({ open, onClose, title, icon, children, width = 'max-w-xl' }: {
  open: boolean;
  onClose: () => void;
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  width?: string;
}) {
  const desktop = useDesktop();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = prev; };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className={cx('fixed inset-0 z-50 flex bg-black/75 backdrop-blur-[2px]', desktop ? 'items-center justify-center p-6' : 'items-end')}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className={cx(
          'relative flex w-full flex-col overflow-hidden border border-navy-border',
          'bg-[linear-gradient(180deg,rgba(8,18,45,0.99),rgba(4,10,24,1))]',
          desktop ? cx(width, 'max-h-[88vh] animate-pop rounded-3xl') : 'max-h-[92dvh] animate-sheet rounded-t-3xl border-b-0',
        )}
      >
        {!desktop && <div className="mx-auto mt-3 h-1 w-11 rounded-full bg-navy-border" />}
        <div className="flex items-center justify-between px-5 pb-4 pt-4">
          <div className="flex items-center gap-2 text-lg font-bold text-white">
            {icon}
            {title}
          </div>
          <button onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-full bg-navy-mid text-mist-soft hover:text-white" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="scroll-thin safe-bottom min-h-0 flex-1 overflow-y-auto px-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
