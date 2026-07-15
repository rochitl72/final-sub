/**
 * Legacy login route — device auth runs at startup; redirect to home.
 */
import { Redirect } from 'expo-router';

export default function LoginScreen() {
  return <Redirect href="/(app)/home" />;
}
