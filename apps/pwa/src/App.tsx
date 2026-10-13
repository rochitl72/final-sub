/**
 * App shell.
 *  Phone / narrow window: Home (chat list) → Chat, one screen at a time.
 *  Desktop (≥1024px):     Home as a 400px sidebar, the open chat (or a welcome pane) on the right.
 */
import { useEffect } from 'react';
import { Navigate, Route, Routes, useSearchParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { catalogsApi } from './lib/api';
import { useAuthStore } from './store/auth';
import { useDesktop } from './hooks/useDesktop';
import { useSessions } from './hooks/useSessions';
import HomeScreen from './screens/HomeScreen';
import ChatScreen from './screens/ChatScreen';
import { DesktopWelcome } from './components/DesktopWelcome';
import { FloatingLogo } from './components/Brand';
import { ToastHost } from './components/Toast';

/** Re-mount the chat per session so input, mode and dialogs never leak between chats. */
function ChatRoute() {
  const [params] = useSearchParams();
  return <ChatScreen key={params.get('sessionId') ?? 'none'} />;
}

function Welcome() {
  const { create } = useSessions();
  return <DesktopWelcome onNew={create} />;
}

export default function App() {
  const isReady = useAuthStore((s) => s.isReady);
  const boot = useAuthStore((s) => s.boot);
  const desktop = useDesktop();

  useEffect(() => {
    boot().then(() => catalogsApi.warmup());
  }, [boot]);

  if (!isReady) {
    return (
      <div className="bg-deep flex h-full flex-col items-center justify-center gap-7">
        <FloatingLogo size={88} />
        <Loader2 className="animate-spin text-brand-pale" />
      </div>
    );
  }

  return (
    <>
      {desktop ? (
        <div className="flex h-full bg-ink">
          <aside className="w-[400px] shrink-0 border-r border-navy-border"><HomeScreen /></aside>
          <section className="min-w-0 flex-1 bg-navy">
            <Routes>
              <Route path="/chat" element={<ChatRoute />} />
              <Route path="*" element={<Welcome />} />
            </Routes>
          </section>
        </div>
      ) : (
        <Routes>
          <Route path="/" element={<HomeScreen />} />
          <Route path="/chat" element={<ChatRoute />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )}
      <ToastHost />
    </>
  );
}
