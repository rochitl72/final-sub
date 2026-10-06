/**
 * Root layout — initializes auth store, handles splash screen.
 */
import { useEffect } from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StyleSheet, View, ActivityIndicator } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as SplashScreen from 'expo-splash-screen';

import { useAuthStore } from '../src/store/authStore';
import { catalogsApi } from '../src/services/api';
import { Colors, Gradients } from '../src/theme';
import { FloatingLogo } from '../src/components/Brand';

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const { isReady, loadFromStorage } = useAuthStore();

  useEffect(() => {
    loadFromStorage().then(() => {
      catalogsApi.warmup().catch(() => {});
      SplashScreen.hideAsync();
    });
  }, []);

  if (!isReady) {
    return (
      <View style={styles.boot}>
        <StatusBar style="light" />
        <LinearGradient colors={Gradients.loginHero} style={StyleSheet.absoluteFill} />
        <FloatingLogo size={88} />
        <ActivityIndicator color={Colors.bluePale} style={styles.bootSpinner} />
      </View>
    );
  }

  return (
    <GestureHandlerRootView style={styles.root}>
      <StatusBar style="light" />
      <Stack screenOptions={{ headerShown: false, animation: 'fade' }}>
        <Stack.Screen name="(app)" options={{ headerShown: false }} />
        <Stack.Screen name="login" options={{ headerShown: false }} />
      </Stack>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.black },
  boot: {
    flex: 1,
    backgroundColor: Colors.black,
    alignItems: 'center',
    justifyContent: 'center',
  },
  bootSpinner: { marginTop: 28 },
});
