/**
 * Chat Store (Zustand)
 * Manages sessions list + active session messages + optimistic UI
 */

import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Session, SessionState, TurnResponse, FineCard, Chip, DetailRow, Explanation } from '../services/api';

export interface ChatMessage {
  id:           string;
  role:         'user' | 'assistant' | 'system';
  content:      string;
  /** Translated text (set by Sarvam Translate when language ≠ en-IN) */
  translatedContent?: string;
  /** True while a translation request is in-flight for this message */
  isTranslating?:     boolean;
  chips?:       Chip[];
  multi_select?: boolean;
  selection_mode?: 'single' | 'multi';
  allow_other?:  boolean;
  fine_card?:   FineCard;
  scenario?:    any;      // multi-person breakdown from the Scenario Engine
  replace_last?: boolean; // this answer supersedes the previous one (clarification / correction)
  detail_table?: DetailRow[];
  explanation?: Explanation;
  allow_text?:  boolean;
  intent?:      string;
  slot?:        string;
  payload?:     any;
  ts:           number;
  pending?:     boolean;   // optimistic — not yet confirmed
}

interface ChatState {
  sessions:        Session[];
  activeSessionId: string | null;
  messages:        ChatMessage[];
  sessionState:    SessionState | null;
  isSending:       boolean;
  isLoadingSessions: boolean;

  setSessions:           (sessions: Session[]) => void;
  setActiveSession:      (id: string | null) => void;
  setMessages:           (msgs: ChatMessage[]) => void;
  addMessage:            (msg: ChatMessage) => void;
  removePendingMessage:  (id: string) => void;
  updateLastMessage:     (update: Partial<ChatMessage>) => void;
  updateMessage:         (id: string, update: Partial<ChatMessage>) => void;
  setSessionState:    (state: SessionState) => void;
  setIsSending:       (v: boolean) => void;
  setLoadingSessions: (v: boolean) => void;
  removeSession:      (id: string) => void;
  applyTurnResponse:  (resp: TurnResponse) => void;
  reset:              () => void;
}

let _msgId = 0;
export function newMsgId() { return `msg_${Date.now()}_${++_msgId}`; }

/**
 * Append an assistant message. When it is an update to the previous answer
 * (a clarification was answered, a fact corrected), the old answer is removed
 * so the conversation shows ONE answer that changes — not a new copy per tap.
 */
export function withAnswer(messages: ChatMessage[], msg: ChatMessage): ChatMessage[] {
  if (!msg.replace_last) return [...messages, msg];
  let i = messages.length - 1;
  for (; i >= 0; i--) {
    const m = messages[i];
    if (m.role === 'assistant' && !m.pending && (m.scenario || m.fine_card || m.chips?.length)) break;
  }
  if (i < 0) return [...messages, msg];
  const rest = [...messages.slice(0, i), ...messages.slice(i + 1)];
  return [...rest, msg];
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions:          [],
  activeSessionId:   null,
  messages:          [],
  sessionState:      null,
  isSending:         false,
  isLoadingSessions: false,

  setSessions: (sessions) => set({ sessions }),

  setActiveSession: (id) => set({ activeSessionId: id, messages: [], sessionState: null }),

  setMessages: (msgs) => set({ messages: msgs }),

  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),

  removePendingMessage: (id) =>
    set((s) => ({ messages: s.messages.filter((m) => m.id !== id) })),

  updateLastMessage: (update) =>
    set((s) => {
      const msgs = [...s.messages];
      if (!msgs.length) return {};
      msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], ...update };
      return { messages: msgs };
    }),

  updateMessage: (id, update) =>
    set((s) => ({
      messages: s.messages.map((m) => m.id === id ? { ...m, ...update } : m),
    })),

  setSessionState: (state) => set({ sessionState: state }),

  setIsSending: (v) => set({ isSending: v }),

  setLoadingSessions: (v) => set({ isLoadingSessions: v }),

  removeSession: (id) =>
    set((s) => ({
      sessions: s.sessions.filter((sess) => sess.id !== id),
      activeSessionId: s.activeSessionId === id ? null : s.activeSessionId,
    })),

  applyTurnResponse: (resp) => {
    const {
      session_state, reply, question, chips, multi_select,
      selection_mode, allow_other,
      fine_card, detail_table, explanation, allow_text, intent, slot, scenario,
      replace_last,
    } = resp as any;
    const content = reply || question || '';
    // Skip empty no-op turns so we don't render blank bubbles.
    if (content || fine_card || chips?.length) {
      const msg: ChatMessage = {
        id:             newMsgId(),
        role:           'assistant',
        content:        content,
        chips:          chips,
        multi_select:   multi_select,
        selection_mode: selection_mode,
        allow_other:    allow_other,
        fine_card:      fine_card,
        scenario:       scenario,
        replace_last:   !!replace_last,
        detail_table:   detail_table,
        explanation:    explanation,
        allow_text:     allow_text,
        intent:         intent,
        slot:           slot,
        ts:             Date.now(),
        pending:        false,
      };
      set((s) => ({ messages: withAnswer(s.messages, msg) }));
    }
    if (session_state) {
      set({ sessionState: session_state });
    }
    // Update the sidebar snippet ONLY when this turn actually produced text.
    // F2 fix: no-op / idle turns have empty content — updating here would blank
    // out the session's existing last_snippet in the sidebar.
    if (content) {
      const stateSnippet = content.slice(0, 80);
      set((s) => ({
        sessions: s.sessions.map((sess) =>
          sess.id === s.activeSessionId
            ? { ...sess, last_snippet: stateSnippet, updated_at: Date.now() }
            : sess
        ),
      }));
    }
  },

  reset: () => set({ sessions: [], activeSessionId: null, messages: [], sessionState: null }),
}));
