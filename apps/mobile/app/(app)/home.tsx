import HomeScreen from '../../src/screens/HomeScreen';
import { DesktopWelcome } from '../../src/components/DesktopWelcome';
import { useDesktop } from '../../src/hooks/useDesktop';

/** On desktop web the chat list is already in the sidebar, so this pane shows a welcome instead. */
export default function Home() {
  return useDesktop() ? <DesktopWelcome /> : <HomeScreen />;
}
