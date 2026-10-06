/**
 * alert.ts — Alert.alert that also works in the browser.
 * react-native-web's Alert.alert is a no-op, which silently broke "Delete
 * chat", "Refresh session" and every error popup on the PWA. On web we map it
 * to window.alert / window.confirm; native keeps the real Alert.
 */
import { Alert as RNAlert, Platform, type AlertButton } from 'react-native';

function webAlert(title: string, message?: string, buttons?: AlertButton[]) {
  const text = message ? `${title}\n\n${message}` : title;
  const actions = (buttons ?? []).filter(b => b.style !== 'cancel');
  const cancel  = (buttons ?? []).find(b => b.style === 'cancel');
  if (!buttons || buttons.length <= 1) {
    window.alert(text);
    buttons?.[0]?.onPress?.();
    return;
  }
  // Confirm dialog: OK → first non-cancel action, Cancel → cancel button
  if (window.confirm(text)) actions[0]?.onPress?.();
  else cancel?.onPress?.();
}

export const Alert = {
  alert(title: string, message?: string, buttons?: AlertButton[]) {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      webAlert(title, message, buttons);
    } else {
      RNAlert.alert(title, message, buttons);
    }
  },
};
