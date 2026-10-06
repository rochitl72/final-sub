/**
 * App entry — bootstrap device auth, then home.
 */
import { View, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Redirect } from 'expo-router';
import { useAuthStore } from '../src/store/authStore';
import { Gradients } from '../src/theme';

export default function Index() {
  const { isReady } = useAuthStore();

  if (!isReady) {
    return (
      <View style={styles.boot}>
        <LinearGradient colors={Gradients.loginHero} style={StyleSheet.absoluteFill} />
      </View>
    );
  }

  return <Redirect href="/(app)/home" />;
}

const styles = StyleSheet.create({ boot: { flex: 1 } });
