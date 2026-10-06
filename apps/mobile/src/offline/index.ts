/**
 * offline/index.ts — device-side dialog fallback
 * ───────────────────────────────────────────────
 * `localTurn` runs the on-device engine and returns a TurnResponse-shaped
 * object so the chat UI renders an offline reply IDENTICALLY to a server reply.
 * This is what makes the app work in airplane mode — no server, no model.
 */

import type { SessionState, TurnResponse } from '../services/api';
import { narrate, OfflineResult, OfflineSession, SEGMENT_FINE_CLASS } from './offlineEngine';

export { narrate } from './offlineEngine';
export { resolve, isDeterministic } from './resolver';
export { quickFine, getViolation } from './graph';

function toOffline(s: SessionState | null): OfflineSession {
  if (!s) return {};
  return {
    state_code: s.state_code,
    city_code: s.city_code,
    city_name: s.city_name,
    road_bucket: s.road_bucket,
    vehicle_segment: s.vehicle_segment,
    // Derive the fine class from the segment so vehicle-specific fines apply.
    vehicle_fine_class: s.vehicle_segment ? (SEGMENT_FINE_CLASS[s.vehicle_segment] ?? null) : null,
    violation_code: s.violation_code,
  };
}

/**
 * Produce a server-shaped turn response entirely on-device.
 * @param sessionId  active session id (echoed back)
 * @param state      current SessionState from the chat store
 * @param text       the user's message
 */
export async function localTurn(
  sessionId: string,
  state: SessionState | null,
  text: string,
): Promise<TurnResponse> {
  const offlineSession = toOffline(state);
  // Rule-based offline engine: fine amounts always come from the local graph.
  const r: OfflineResult = narrate(offlineSession, text);

  const nextState: SessionState = {
    ...(state as SessionState),
    violation_code: r.violationCode ?? state?.violation_code ?? null,
    // Vehicle inferred on-device ("on my scooter", helmet → two-wheeler).
    vehicle_segment: offlineSession.vehicle_segment ?? state?.vehicle_segment ?? null,
  } as SessionState;

  return {
    session_id: sessionId,
    intent: r.intent,
    reply: r.reply,
    fine_card: (r.card as any) ?? undefined,
    session_state: nextState,
    mode: 'dynamic',
  } as TurnResponse;
}

/** True when the device should answer locally instead of calling the server. */
export function shouldAnswerLocally(connectivity: string): boolean {
  return connectivity !== 'server_ok';
}
