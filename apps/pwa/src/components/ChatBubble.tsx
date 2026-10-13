import { useState, type ReactNode } from 'react';
import { ChevronDown, Globe, Loader2 } from 'lucide-react';
import type { ChatMessage } from '../store/chat';
import type { Explanation } from '../lib/api';
import { cx } from '../lib/format';
import { Logo } from './Brand';
import { FineCard } from './FineCard';
import { MultiSelect } from './MultiSelect';
import { ScenarioCards } from './ScenarioCards';
import { TtsButton } from './TtsButton';

/** **bold** + line breaks, nothing else — replies are plain text from the backend. */
function RichText({ text }: { text: string }) {
  const lines = text.split('\n');
  return (
    <>
      {lines.map((line, li) => (
        <span key={li}>
          {line.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((p, i) =>
            p.startsWith('**') && p.endsWith('**') ? <strong key={i} className="font-bold text-white">{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>,
          )}
          {li < lines.length - 1 && <br />}
        </span>
      ))}
    </>
  );
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1.5 px-4 py-3.5" aria-label="DriveLegal is typing">
      {[0, 150, 300].map((d) => (
        <span key={d} className="h-[7px] w-[7px] animate-dot rounded-full bg-brand-pale" style={{ animationDelay: `${d}ms` }} />
      ))}
    </div>
  );
}

function Why({ e }: { e: Explanation }) {
  const [open, setOpen] = useState(false);
  const rows = [
    ['Matched', e.violation_matched],
    ['How', [e.match_method, e.match_confidence].filter(Boolean).join(' · ')],
    ['Fine source', e.fine_source],
    ['Reasoning', e.reasoning],
    ['Data', e.data_sources?.join(', ')],
  ].filter(([, v]) => !!v) as [string, string][];
  if (!rows.length) return null;
  return (
    <div className="rounded-2xl border border-navy-border bg-navy-mid/60">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center justify-between px-3.5 py-2 text-xs font-semibold text-mist-soft hover:text-white">
        How this was worked out
        <ChevronDown size={14} className={cx('transition', open && 'rotate-180')} />
      </button>
      {open && (
        <dl className="space-y-1.5 px-3.5 pb-3 text-xs">
          {rows.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[88px_1fr] gap-2">
              <dt className="text-mist-soft">{k}</dt>
              <dd className="text-mist">{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export function ChatBubble({ message, interactive = true, onMultiSubmit, sarvamAvailable, onTtsError }: {
  message: ChatMessage;
  /** Only the newest answer's options can be answered; older ones are history. */
  interactive?: boolean;
  onMultiSubmit: (ids: string[], otherText?: string) => void;
  sarvamAvailable: boolean;
  onTtsError: (m: string) => void;
}) {
  const isUser = message.role === 'user';
  const display = message.translatedContent ?? message.content;
  const sc = message.scenario;
  // Structured multi-person answer: head text → person cards → tail text → options.
  const structured = !isUser && !message.translatedContent && !!sc?.people?.length && sc.head != null;
  const text = structured ? sc!.head || '' : display;
  const showMcq = !isUser && !!message.chips?.length;
  const mcq: ReactNode = showMcq ? (
    <MultiSelect options={message.chips!} mode={message.selection_mode === 'single' ? 'single' : 'multi'} allowOther={message.allow_other === true} disabled={!interactive} onSubmit={onMultiSubmit} />
  ) : null;

  if (isUser) {
    return (
      <div className="flex animate-slide-r justify-end px-4 py-1">
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl rounded-br-md bg-user-bubble px-4 py-3 text-[15px] leading-relaxed text-white shadow-lg shadow-black/30 lg:max-w-[70%]">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex animate-slide-l gap-2.5 px-4 py-1">
      <Logo size={32} className="mt-1" />
      <div className="flex min-w-0 max-w-[88%] flex-1 flex-col gap-2.5 lg:max-w-[78%]">
        {(text || message.pending) && (
          <div className="self-start overflow-hidden rounded-3xl rounded-bl-md border border-navy-border bg-navy-light">
            {message.pending ? (
              <TypingDots />
            ) : (
              <>
                <div className="break-words px-4 pb-2 pt-3 text-[15px] leading-relaxed text-mist"><RichText text={text} /></div>
                <div className="flex items-center gap-2 px-2.5 pb-1.5">
                  {message.isTranslating ? (
                    <span className="flex items-center gap-1 px-1.5 py-1 text-xs italic text-brand-light"><Loader2 size={11} className="animate-spin" /> Translating…</span>
                  ) : (
                    <TtsButton text={display} sarvamAvailable={sarvamAvailable} onError={onTtsError} />
                  )}
                  {message.translatedContent && <span className="flex items-center gap-1 text-[10px] italic text-mist-soft"><Globe size={10} /> translated</span>}
                </div>
              </>
            )}
          </div>
        )}

        {showMcq && !structured && mcq}
        {structured && <ScenarioCards scenario={sc!} />}
        {structured && !!sc!.tail && (
          <div className="self-start rounded-3xl rounded-bl-md border border-navy-border bg-navy-light px-4 py-3 text-[15px] leading-relaxed text-mist">
            <RichText text={sc!.tail!} />
          </div>
        )}
        {message.fine_card && !structured && <FineCard card={message.fine_card} detailTable={message.detail_table} />}
        {message.explanation && !message.pending && <Why e={message.explanation} />}
        {showMcq && structured && mcq}
      </div>
    </div>
  );
}
