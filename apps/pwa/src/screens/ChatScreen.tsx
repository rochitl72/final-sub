/**
 * ChatScreen — one conversation, both modes.
 *   Calculator (static): chip-driven slot filling → exact fine card.
 *   AI Chat (dynamic):   free text → multi-person scenario breakdown; the backend LLM may refine it in the background.
 * Offline / no server → the on-device rule engine answers with the same card shapes.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowUp, Calculator, ChevronLeft, Loader2, MapPin, Sparkles, Undo2 } from 'lucide-react';
import { dialogApi, sessionsApi, setPreferRulesMode, type SessionMode, type TurnResponse } from '../lib/api';
import { translate, type Language } from '../lib/sarvam';
import { cx } from '../lib/format';
import { localTurn, shouldAnswerLocally } from '../offline';
import { fromHistory, newMsgId, useChatStore } from '../store/chat';
import { usePrefs } from '../store/prefs';
import { FALLBACK_TOAST, useConnectivity } from '../hooks/useConnectivity';
import { useDesktop } from '../hooks/useDesktop';
import { ChatBubble } from '../components/ChatBubble';
import { ProgressBar } from '../components/ProgressBar';
import { LocationModal, type LocTab } from '../components/LocationModal';
import { LanguagePicker } from '../components/LanguagePicker';
import { EnginePill } from '../components/EnginePill';
import { toast } from '../components/Toast';

const EXAMPLES = [
  'My son (16) was riding my scooter without a helmet',
  'Caught at 95 km/h in a 40 zone near a school',
  'Drunk driving and jumped a signal — what happens?',
];

export default function ChatScreen() {
  const navigate = useNavigate();
  const desktop = useDesktop();
  const [params] = useSearchParams();
  const sessionId = params.get('sessionId') || '';

  const messages = useChatStore((s) => s.messages);
  const sessionState = useChatStore((s) => s.sessionState);
  const isSending = useChatStore((s) => s.isSending);
  const { addMessage, removeMessage, updateMessage, setMessages, setSessionState, setIsSending, applyTurnResponse, openSession } = useChatStore.getState();
  const language = usePrefs((s) => s.language);
  const toggleAi = usePrefs((s) => s.toggleAiPreference);

  const [mode, setMode] = useState<SessionMode>((params.get('mode') as SessionMode) || 'static');
  const [input, setInput] = useState('');
  const [locOpen, setLocOpen] = useState(false);
  const [locTab, setLocTab] = useState<LocTab>('gps');
  const [refining, setRefining] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const conn = useConnectivity((r) => toast(FALLBACK_TOAST[r]));
  const connRef = useRef(conn.connectivity);
  connRef.current = conn.connectivity;
  const canTranslate = conn.serverReachable;

  useEffect(() => { setPreferRulesMode(conn.effectiveMode === 'rules'); }, [conn.effectiveMode]);

  // ── Background refinement: poll until the LLM-refined answer replaces the instant one
  const pollId = useRef(0);
  useEffect(() => () => { pollId.current++; }, []);
  const pollUpdates = useCallback(async () => {
    const my = ++pollId.current;
    setRefining(true);
    const started = Date.now();
    try {
      while (Date.now() - started < 120_000) {
        await new Promise((r) => setTimeout(r, 2500));
        if (pollId.current !== my) return;
        let r;
        try { r = await dialogApi.updates(sessionId); } catch { continue; }
        if (pollId.current !== my) return;
        for (const u of r.updates || []) useChatStore.getState().applyTurnResponse(u);
        if (!r.refining) break;
      }
    } finally {
      if (pollId.current === my) setRefining(false);
    }
  }, [sessionId]);

  const finishTurn = useCallback((resp: TurnResponse) => {
    pollId.current++;
    setRefining(false);
    applyTurnResponse(resp);
    if (resp.refining) pollUpdates();
  }, [applyTurnResponse, pollUpdates]);

  /** Run one server call with a typing indicator; fall back on-device when `fallbackText` is given. */
  const runTurn = useCallback(async (call: () => Promise<TurnResponse>, fallbackText?: string) => {
    if (!sessionId) return;
    setIsSending(true);
    const pending = newMsgId();
    addMessage({ id: pending, role: 'assistant', content: '', pending: true, ts: Date.now() });
    try {
      let resp: TurnResponse;
      const local = () => localTurn(sessionId, useChatStore.getState().sessionState, fallbackText ?? '');
      if (fallbackText !== undefined && shouldAnswerLocally(connRef.current)) resp = await local();
      else {
        try { resp = await call(); } catch (e) { if (fallbackText === undefined) throw e; resp = await local(); }
      }
      removeMessage(pending);
      finishTurn(resp);
    } catch {
      removeMessage(pending);
      addMessage({ id: newMsgId(), role: 'assistant', content: 'Something went wrong. Please try again.', ts: Date.now() });
    } finally {
      setIsSending(false);
    }
  }, [sessionId, addMessage, removeMessage, setIsSending, finishTurn]);

  // ── Load history (or greet) when the chat opens
  useEffect(() => {
    if (!sessionId) return;
    openSession(sessionId);
    let alive = true;
    sessionsApi.history(sessionId)
      .then(({ messages: hist, session_state }) => {
        if (!alive) return;
        setMessages(fromHistory(hist));
        setSessionState(session_state);
        if (session_state?.mode) setMode(session_state.mode as SessionMode);
        setLoaded(true);
        if (!hist.length) runTurn(() => dialogApi.turn(sessionId), 'hi');
      })
      .catch(() => { if (alive) { setLoaded(true); runTurn(() => dialogApi.turn(sessionId), 'hi'); } });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // ── Translation: newest reply automatically; whole chat when the language changes
  const lastAuto = useRef<string | null>(null);
  const translateMsg = useCallback(async (id: string, text: string, lang: string) => {
    if (lang === 'en-IN' || !text.trim()) return;
    updateMessage(id, { isTranslating: true });
    const t = await translate(text, lang);
    updateMessage(id, { translatedContent: t && t !== text ? t : undefined, isTranslating: false });
  }, [updateMessage]);

  useEffect(() => {
    if (!canTranslate || language.code === 'en-IN') return;
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant' || last.pending || !last.content) return;
    if (last.id === lastAuto.current || last.translatedContent || last.isTranslating) return;
    lastAuto.current = last.id;
    translateMsg(last.id, last.content, language.code);
  }, [messages, canTranslate, language.code, translateMsg]);

  const retranslating = useRef(0);
  const onLanguageChange = useCallback(async (l: Language) => {
    const run = ++retranslating.current;
    const bot = useChatStore.getState().messages.filter((m) => m.role === 'assistant' && m.content);
    if (l.code === 'en-IN') {
      bot.forEach((m) => updateMessage(m.id, { translatedContent: undefined, isTranslating: false }));
      return;
    }
    toast(`${l.label} — translating the conversation…`);
    for (const m of bot) {
      if (retranslating.current !== run) return;
      updateMessage(m.id, { translatedContent: undefined });
      await translateMsg(m.id, m.content, l.code);
    }
  }, [translateMsg, updateMessage]);

  // ── Keep the newest message in view: follow content growth (cards, translations, refinements)
  // while the reader is at the bottom; leave them alone once they scroll up to read.
  const scrollRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const last = messages[messages.length - 1];
  const lastAssistantId = [...messages].reverse().find((m) => m.role === 'assistant' && !m.pending)?.id;
  useEffect(() => {
    const el = scrollRef.current, content = contentRef.current;
    if (!el || !content) return;
    const nearBottom = () => el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    // Only the reader's own scrolling can un-stick the view; programmatic scrolls and content
    // growth fire 'scroll' too, and must never turn following off.
    const onScroll = () => { if (nearBottom()) stick.current = true; };
    const onUserScroll = () => requestAnimationFrame(() => { stick.current = nearBottom(); });
    const ro = new ResizeObserver(() => { if (stick.current) el.scrollTop = el.scrollHeight; });
    el.addEventListener('scroll', onScroll, { passive: true });
    el.addEventListener('wheel', onUserScroll, { passive: true });
    el.addEventListener('touchmove', onUserScroll, { passive: true });
    el.addEventListener('keydown', onUserScroll);
    ro.observe(content);
    return () => {
      el.removeEventListener('scroll', onScroll);
      el.removeEventListener('wheel', onUserScroll);
      el.removeEventListener('touchmove', onUserScroll);
      el.removeEventListener('keydown', onUserScroll);
      ro.disconnect();
    };
  }, []);
  useEffect(() => {
    // A message I just sent always brings the view down.
    if (last?.role === 'user' || last?.pending) stick.current = true;
    if (stick.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length, last?.role, last?.pending]);

  // ── Actions
  const send = (text?: string) => {
    const msg = (text ?? input).trim();
    if (!msg || isSending) return;
    setInput('');
    addMessage({ id: newMsgId(), role: 'user', content: msg, ts: Date.now() });
    runTurn(() => dialogApi.turn(sessionId, msg), msg);
  };

  const submitOptions = (ids: string[], other?: string) => {
    if (ids.includes('map')) { setLocTab('map'); setLocOpen(true); return; }
    stick.current = true;
    runTurn(() => dialogApi.turnMulti(sessionId, ids, other));
  };

  const toggleMode = async () => {
    if (isSending) return;
    const next: SessionMode = mode === 'static' ? 'dynamic' : 'static';
    setMode(next);
    setIsSending(true);
    const pending = newMsgId();
    addMessage({ id: pending, role: 'assistant', content: '', pending: true, ts: Date.now() });
    try {
      const resp = await dialogApi.setMode(sessionId, next);
      removeMessage(pending);
      setMode((resp.mode || resp.session_state?.mode || next) as SessionMode);
      finishTurn(resp);
    } catch {
      removeMessage(pending);
      setMode(mode);
      toast('Could not switch mode — server unreachable');
    } finally {
      setIsSending(false);
    }
  };

  const setManualLocation = (state?: string, city?: string, label?: string) => {
    if (label) toast(`Location set: ${label}`);
    runTurn(() => dialogApi.manualLocation(sessionId, { state_code: state, city_code: city }));
  };
  const setPinLocation = (lat: number, lng: number, label?: string) => {
    if (label) toast(`Location set: ${label}`);
    runTurn(() => dialogApi.pinDrop(sessionId, lat, lng));
  };
  const undoLocation = () => runTurn(() => dialogApi.revertLocation(sessionId));
  const openLocation = (tab: LocTab) => { setLocTab(tab); setLocOpen(true); };

  const hasState = !!sessionState?.state_code;
  const allowText = !last || last.role !== 'assistant' || last.allow_text !== false;
  const showLocBanner = loaded && !hasState && !sessionState?.geo_mode && !locOpen;
  const title = sessionState?.city_name || (sessionState?.state_code ? `State: ${sessionState.state_code}` : 'New session');
  const onlyGreeting = mode === 'dynamic' && messages.filter((m) => m.role === 'user').length === 0;

  if (!sessionId) return <Navigate to="/" replace />;

  return (
    <div className="bg-deep flex h-full min-h-0 flex-col">
      {/* Header */}
      <header className={cx('safe-top flex items-center gap-2 border-b border-navy-border px-3 pb-3', desktop && 'pt-3')}>
        {!desktop && (
          <button type="button" onClick={() => navigate('/')} className="icon-btn" aria-label="Back to chats"><ChevronLeft size={22} /></button>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[15px] font-bold text-white">{title}</div>
          <div className="mt-0.5 flex items-center gap-1.5 text-xs text-mist-soft">
            {mode === 'static' ? <Calculator size={12} /> : <Sparkles size={12} />}
            {mode === 'static' ? 'Calculator' : 'AI Chat'}
            {mode === 'dynamic' && (
              <EnginePill
                variant={conn.pill}
                onToggle={() => {
                  conn.resetToasts();
                  const p = toggleAi();
                  toast(p === 'cloud' ? 'AI on — the model writes answers, facts come from the law graph' : 'Rules only — no AI calls');
                  conn.refresh();
                }}
              />
            )}
          </div>
        </div>
        <LanguagePicker enabled={canTranslate} onChange={onLanguageChange} />
        <button type="button" onClick={() => openLocation(hasState ? 'browse' : 'gps')} className={cx('icon-btn', hasState && 'border-brand text-brand-pale')} aria-label="Set location" title="Set location">
          <MapPin size={18} />
        </button>
        <button type="button" onClick={toggleMode} disabled={isSending} className="icon-btn text-brand-pale" aria-label={mode === 'static' ? 'Switch to AI Chat' : 'Switch to Calculator'} title={mode === 'static' ? 'Switch to AI Chat' : 'Switch to Calculator'}>
          {mode === 'static' ? <Sparkles size={17} /> : <Calculator size={17} />}
        </button>
      </header>

      {mode === 'static' && <ProgressBar state={sessionState} />}

      {/* Messages */}
      <main ref={scrollRef} className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <div ref={contentRef} className="mx-auto w-full max-w-4xl py-4">
          {!loaded && messages.length === 0 && (
            <div className="flex justify-center py-16 text-mist-soft"><Loader2 className="animate-spin" /></div>
          )}
          {messages.map((m) => (
            <ChatBubble key={m.id} message={m} interactive={m.id === lastAssistantId && !isSending} onMultiSubmit={submitOptions} sarvamAvailable={conn.sarvamAvailable} onTtsError={toast} />
          ))}
          {onlyGreeting && hasState && !isSending && loaded && (
            <div className="flex flex-wrap gap-2 px-4 pl-[3.6rem] pt-2">
              {EXAMPLES.map((e) => (
                <button key={e} type="button" onClick={() => send(e)} className="rounded-2xl border border-navy-border bg-navy-mid px-3.5 py-2 text-left text-sm text-mist transition hover:border-brand-light hover:text-white">
                  {e}
                </button>
              ))}
            </div>
          )}
          <div className="h-2" />
        </div>
      </main>

      {/* Footer */}
      <footer className="safe-bottom border-t border-navy-border bg-[linear-gradient(180deg,rgba(10,22,40,0.98),rgba(5,13,31,1))] px-3 pt-3">
        <div className="mx-auto w-full max-w-4xl">
          {showLocBanner && (
            <button type="button" onClick={() => openLocation('gps')} className="mb-3 flex w-full items-center justify-center gap-2 rounded-2xl border border-brand/40 bg-brand/15 p-3 text-sm font-medium text-brand-pale hover:bg-brand/25">
              <MapPin size={15} /> Set your location to get exact local fines
            </button>
          )}

          {mode === 'dynamic' && (
            <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); send(); }}>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
                placeholder={allowText ? 'Describe what happened, or ask about any traffic rule…' : 'Pick an option above to continue…'}
                disabled={!allowText || isSending}
                rows={1}
                maxLength={2000}
                className="scroll-thin max-h-36 min-h-[48px] flex-1 resize-none rounded-2xl border border-navy-border bg-navy-mid px-4 py-3 text-[15px] leading-normal text-white placeholder:text-mist-dim focus:border-brand focus:outline-none disabled:opacity-50 [field-sizing:content]"
                aria-label="Message"
              />
              <button type="submit" disabled={!input.trim() || isSending || !allowText} className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gloss text-white shadow-glow transition active:scale-95 disabled:opacity-40 disabled:shadow-none" aria-label="Send">
                {isSending ? <Loader2 size={20} className="animate-spin" /> : <ArrowUp size={22} />}
              </button>
            </form>
          )}

          {(refining || hasState) && (
            <div className="mt-2 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pb-1 text-xs text-mist-soft">
              {refining && <span className="flex items-center gap-1.5"><Loader2 size={12} className="animate-spin" /> Checking the details with the AI — the answer may update</span>}
              {hasState && (
                <button type="button" onClick={() => openLocation('browse')} className="flex items-center gap-1 py-1 hover:text-white"><MapPin size={12} /> Change location</button>
              )}
              {hasState && sessionState?.has_prev_location && (
                <button type="button" onClick={undoLocation} className="flex items-center gap-1 py-1 hover:text-white"><Undo2 size={12} /> Undo last change</button>
              )}
            </div>
          )}
        </div>
      </footer>

      <LocationModal open={locOpen} initialTab={locTab} onClose={() => setLocOpen(false)} onManual={setManualLocation} onPin={setPinLocation} />
    </div>
  );
}
