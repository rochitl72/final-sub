/**
 * Main app stack — device auth is bootstrapped in root layout before entry.
 */
import { Stack } from 'expo-router';

export default function AppLayout() {
  return (
    <Stack screenOptions={{ headerShown: false, animation: 'slide_from_right' }}>
      <Stack.Screen name="home" options={{ animation: 'fade' }} />
      <Stack.Screen name="chat" options={{ animation: 'slide_from_right', gestureEnabled: true }} />
    </Stack>
  );
}
