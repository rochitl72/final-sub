import { Platform, useWindowDimensions } from 'react-native';

/** True on the web build when the window is wide enough for the two-pane desktop layout. */
export const DESKTOP_MIN_WIDTH = 1024;
export function useDesktop(): boolean {
  const { width } = useWindowDimensions();
  return Platform.OS === 'web' && width >= DESKTOP_MIN_WIDTH;
}
