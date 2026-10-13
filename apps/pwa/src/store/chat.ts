/**
 * Chat store — session list, the open conversation, and optimistic UI.
 */
import { create } from 'zustand';
import type {
  Chip, DetailRow, Explanation, FineCard, HistoryMessage, Scenario, Session, SessionState, TurnResponse,
} from '../lib/api';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  /** Sarvam translation (when the reply language isn't English) */
  translatedContent?: string;
  isTranslating?: boolean;
  chips?: Chip[];
  selection_mode?: 'single' | 'multi';
  allow_other?: boolean;
  fine_card?: FineCard;
  scenario?: Scenario;
  /** This answer supersedes the previous one (clarification answered / fact corrected). */
  replace_last?: boolean;
  detail_table?: DetailRow[];
  explanation?: Explanation;
  allow_text?: boolean;
  intent?: string;
  ts: number;
  pending?: boolean;
}

let _id = 0;
export const newMsgId = () => `msg_${Date.now()}_${++_id}`;

/**
 * Append an assistant message. If it updates the previous answer, the old answer is removed,
 * so the conversation shows ONE answer that changes — not a new copy per tap.
 */
export function withAnswer(messages: ChatMessage[], msg: ChatMessage): ChatMessage[] {
  if (!msg.replace_last) return [...messages, msg];
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === 'assistant' && !m.pending && (m.scenario || m.fine_card || m.chips?.length)) {
      return [...messages.slice(0, i), ...messages.slice(i + 1), msg];
    }
  }
  return [...messages, msg];
}

function fromTurn(resp: TurnResponse): ChatMessage | null {
  const content = resp.reply || resp.question || '';
  if (!content && !resp.fine_card && !resp.chips?.length && !resp.scenario?.people?.length) return null;
  return {
    id: newMsgId(),
    role: 'assistant',
    content,
    chips: resp.chips,
    selection_mode: resp.selection_mode,
    allow_other: resp.allow_other,
    fine_card: resp.fine_card,
    scenario: resp.scenario,
    replace_last: !!resp.replace_last,
    detail_table: resp.detail_table,
    explanation: resp.explanation,
    allow_text: resp.allow_text,
    intent: resp.intent,
    ts: Date.now(),
  };
}

export function fromHistory(hist: HistoryMessage[]): ChatMessage[] {
  return hist
    .map<ChatMessage>((m) => ({
      id: newMsgId(),
      role: m.role,
      content: m.content || '',
      chips: m.payload?.chips,
      selection_mode: m.payload?.selection_mode,
      allow_other: m.payload?.allow_other,
      fine_card: m.payload?.fine_card,
      scenario: m.payload?.scenario,
      replace_last: !!m.payload?.replace_last,
      detail_table: m.payload?.detail_table,
      explanation: m.payload?.explanation,
      allow_text: m.payload?.allow_text,
      intent: m.payload?.intent,
      ts: m.created_at,
    }))
    .reduce<ChatMessage[]>((acc, m) => withAnswer(acc, m), []);
}

interface ChatState {
  sessions: Session[];
  activeSessionId: string | null;
  messages: ChatMessage[];
  sessionState: SessionState | null;
  isSending: boolean;

  setSessions: (s: Session[]) => void;
  upsertSessionTop: (s: Session) => void;
  removeSession: (id: string) => void;
  openSession: (id: string | null) => void;
  setMessages: (m: ChatMessage[]) => void;
  addMessage: (m: ChatMessage) => void;
  removeMessage: (id: string) => void;
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void;
  setSessionState: (s: SessionState | null) => void;
  setIsSending: (v: boolean) => void;
  applyTurnResponse: (resp: TurnResponse) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  sessionState: null,
  isSending: false,

  setSessions: (sessions) => set({ sessions }),
  upsertSessionTop: (s) => set((st) => ({ sessions: [s, ...st.sessions.filter((x) => x.id !== s.id)] })),
  removeSession: (id) =>
    set((st) => ({
      sessions: st.sessions.filter((s) => s.id !== id),
      activeSessionId: st.activeSessionId === id ? null : st.activeSessionId,
    })),
  openSession: (id) => set({ activeSessionId: id, messages: [], sessionState: null, isSending: false }),
  setMessages: (messages) => set({ messages }),
  addMessage: (m) => set((st) => ({ messages: [...st.messages, m] })),
  removeMessage: (id) => set((st) => ({ messages: st.messages.filter((m) => m.id !== id) })),
  updateMessage: (id, patch) => set((st) => ({ messages: st.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) })),
  setSessionState: (sessionState) => set({ sessionState }),
  setIsSending: (isSending) => set({ isSending }),

  applyTurnResponse: (resp) => {
    const msg = fromTurn(resp);
    if (msg) set((st) => ({ messages: withAnswer(st.messages, msg) }));
    if (resp.session_state) set({ sessionState: resp.session_state });
    // Update the sidebar snippet only when the turn produced text (idle turns would blank it).
    const text = msg?.content || msg?.scenario?.head || '';
    if (text) {
      set((st) => ({
        sessions: st.sessions.map((s) =>
          s.id === st.activeSessionId ? { ...s, last_snippet: text.slice(0, 80), updated_at: Date.now() } : s,
        ),
      }));
    }
  },
}));
