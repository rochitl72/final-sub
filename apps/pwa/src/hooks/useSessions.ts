import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { sessionsApi, type Session, type SessionMode } from '../lib/api';
import { useChatStore } from '../store/chat';
import { toast } from '../components/Toast';

/** Create / open / delete chats — shared by the sidebar, the phone home screen and the desktop welcome pane. */
export function useSessions() {
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try { useChatStore.getState().setSessions(await sessionsApi.list()); } catch { /* offline — keep the list we have */ }
  }, []);

  const open = useCallback((s: Pick<Session, 'id' | 'mode'>) => {
    navigate(`/chat?sessionId=${encodeURIComponent(s.id)}&mode=${s.mode}`);
  }, [navigate]);

  const create = useCallback(async (mode: SessionMode) => {
    try {
      const resp = await sessionsApi.create(mode);
      const now = Date.now();
      useChatStore.getState().upsertSessionTop({ id: resp.session_id, title: 'New chat', mode, created_at: now, updated_at: now, last_snippet: '' });
      open({ id: resp.session_id, mode });
    } catch {
      toast('Could not start a chat — is the backend running?');
    }
  }, [open]);

  const remove = useCallback(async (s: Session, activeId: string | null) => {
    if (!window.confirm(`Delete "${s.title || 'this chat'}"?`)) return;
    useChatStore.getState().removeSession(s.id);
    if (s.id === activeId) navigate('/', { replace: true });
    try { await sessionsApi.delete(s.id); } catch { /* offline */ }
  }, [navigate]);

  return { load, open, create, remove };
}
