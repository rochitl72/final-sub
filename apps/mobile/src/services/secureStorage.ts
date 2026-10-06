/** SecureStore on iOS/Android; localStorage on the web (PWA), where expo-secure-store isn't available. */
import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

const web = Platform.OS === 'web';
const ls = () => { try { return globalThis.localStorage; } catch { return undefined; } };

export const secureStorage = {
  getItemAsync: async (k: string): Promise<string | null> => (web ? ls()?.getItem(k) ?? null : SecureStore.getItemAsync(k)),
  setItemAsync: async (k: string, v: string): Promise<void> => { if (web) ls()?.setItem(k, v); else await SecureStore.setItemAsync(k, v); },
  deleteItemAsync: async (k: string): Promise<void> => { if (web) ls()?.removeItem(k); else await SecureStore.deleteItemAsync(k); },
};
