/**
 * Main app stack — device auth is bootstrapped in root layout before entry.
 *
 * Phones (and narrow browser windows): the normal stack — Home → Chat.
 * Desktop web (≥1024px): the SAME screens side by side — Home (chat list) as a sidebar,
 * the open chat on the right. Only the layout changes; every component is the mobile one.
 */
import { Slot, Stack } from 'expo-router';
import { StyleSheet, View } from 'react-native';
import HomeScreen from '../../src/screens/HomeScreen';
import { useDesktop } from '../../src/hooks/useDesktop';
import { Colors } from '../../src/theme';

export default function AppLayout() {
  const desktop = useDesktop();
  if (desktop) {
    return (
      <View style={s.row}>
        <View style={s.sidebar}><HomeScreen /></View>
        <View style={s.main}><View style={s.column}><Slot /></View></View>
      </View>
    );
  }
  return (
    <Stack screenOptions={{ headerShown: false, animation: 'slide_from_right' }}>
      <Stack.Screen name="home" options={{ animation: 'fade' }} />
      <Stack.Screen name="chat" options={{ animation: 'slide_from_right', gestureEnabled: true }} />
    </Stack>
  );
}

const s = StyleSheet.create({
  row:     { flex: 1, flexDirection: 'row', backgroundColor: Colors.black, height: '100%' as any },
  sidebar: { width: 400, borderRightWidth: 1, borderRightColor: Colors.navyBorder, overflow: 'hidden' },
  main:    { flex: 1, minWidth: 0, overflow: 'hidden', backgroundColor: Colors.navy, alignItems: 'center' },
  // keep chat bubbles and option lists at a readable width on big monitors
  column:  { flex: 1, width: '100%', maxWidth: 1040, borderLeftWidth: 1, borderRightWidth: 1, borderColor: 'rgba(26,58,107,0.45)' },
});
