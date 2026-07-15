/**
 * offline/index.ts — device-side dialog fallback
 * ───────────────────────────────────────────────
 * `localTurn` runs the on-device engine and returns a TurnResponse-shaped
 * object so the chat UI renders an offline reply IDENTICALLY to a server reply.
 * This is what makes the app work in airplane mode — no server, no model.
 */

import type { SessionState, TurnResponse } from '../services/api';
import { narrate, OfflineResult, OfflineSession } from './offlineEngine';
import { narrateWithSLM } from './slm';

export { narrate } from './offlineEngine';
export { resolve, isDeterministic } from './resolver';
export { quickFine, getViolation } from './graph';
export { setOnDeviceLLM, onDeviceLLMReady } from './slm';
export type { OnDeviceLLM } from './slm';

function toOffline(s: SessionState | null): OfflineSession {
  if (!s) return {};
  return {
    state_code: s.state_code,
    city_code: s.city_code,
    city_name: s.city_name,
    road_bucket: s.road_bucket,
    vehicle_segment: s.vehicle_segment,
    // Mobile SessionState carries segment but not the fine-class/label — the
    // cascade safely falls back to the non-vehicle-specific fine when null.
    vehicle_fine_class: null,
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
  // Tier 2: on-device neural model (if a runtime is registered). Falls back to
  // Tier 3 deterministic engine otherwise. Fine amounts stay graph-exact.
  const r: OfflineResult =
    (await narrateWithSLM(offlineSession, text)) ?? narrate(offlineSession, text);

  const nextState: SessionState = {
    ...(state as SessionState),
    violation_code: r.violationCode ?? state?.violation_code ?? null,
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
