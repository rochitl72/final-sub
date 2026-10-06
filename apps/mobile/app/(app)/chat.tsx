import React from 'react';
import { useLocalSearchParams } from 'expo-router';
import ChatScreen from '../../src/screens/ChatScreen';

// Keyed by session so opening another chat (or "New chat") always gives a
// fresh screen. On the desktop two-pane layout the route stays mounted when
// only the ?sessionId= param changes, which otherwise leaked the old chat's
// input, mode and modal state into the new one.
export default function ChatRoute() {
  const { sessionId } = useLocalSearchParams<{ sessionId?: string }>();
  return <ChatScreen key={sessionId ?? 'none'} />;
}
