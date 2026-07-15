/**
 * slm.ts — on-device Small Language Model tier (Tier 2 of the offline cascade)
 * ────────────────────────────────────────────────────────────────────────────
 * This is the pluggable seam for a neural on-device model (Gemma 3n via
 * MediaPipe, Llama 3.2 1B via ExecuTorch, or WebLLM on the web). It is OFF until
 * a runtime is registered with `setOnDeviceLLM()`, so the app ships working
 * (deterministic Tier 3) and lights up Tier 2 the moment a model is wired in.
 *
 * KEY PRINCIPLE — the model NARRATES, the graph ADJUDICATES.
 * The exact fine amount is ALWAYS taken from the local graph (quickFine), never
 * from the model. The SLM only rephrases / handles fuzzy intent. This is what
 * keeps offline answers trustworthy: a 1B model cannot invent a ₹ value.
 *
 * See docs/OFFLINE_AI.md for how to register a real runtime.
 */

import { getViolation, quickFine } from './graph';
import { narrate, OfflineResult, OfflineSession } from './offlineEngine';
import { resolve } from './resolver';

export interface OnDeviceLLM {
  /** True once the model file is downloaded and the runtime is initialised. */
  isReady(): boolean;
  /** Generate a completion for a fully-formed prompt. */
  generate(prompt: string, opts?: { maxTokens?: number; temperature?: number }): Promise<string>;
}

let _model: OnDeviceLLM | null = null;

/** Register a concrete on-device runtime (MediaPipe / ExecuTorch / WebLLM). */
export function setOnDeviceLLM(model: OnDeviceLLM | null): void {
  _model = model;
}

export function onDeviceLLMReady(): boolean {
  return !!_model && _model.isReady();
}

/**
 * Retrieval step: pull the most relevant grounded facts from the local graph
 * for this query. This is the "R" in on-device RAG — the model is only ever
 * shown verified facts, never asked to recall fines from its weights.
 */
export function buildRagContext(session: OfflineSession, text: string): string {
  const ranked = resolve(text, session.road_bucket, session.vehicle_fine_class).slice(0, 4);
  if (!ranked.length) return 'No specific violation matched in the local dataset.';
  const lines = ranked.map(([code]) => {
    const v = getViolation(code);
    const card = quickFine(code, session.state_code, session.city_code, session.vehicle_fine_class);
    const fine = card?.fine_first != null ? `₹${card.fine_first}` : 'varies';
    const sec = v?.mv_section ? ` (MV Act §${v.mv_section})` : '';
    return `- ${v?.name || code}${sec}: first-offence ${fine}`;
  });
  return `RELEVANT LOCAL FACTS (use ONLY these; do not invent amounts):\n${lines.join('\n')}`;
}

const SYSTEM = (
  'You are DriveLegal, a concise Indian road-law assistant working fully offline. ' +
  'Answer in 2-3 sentences using ONLY the RELEVANT LOCAL FACTS provided. ' +
  'Never state a fine amount that is not in those facts. Cite the MV Act section.'
);

/**
 * Tier-2 attempt. Returns a narrated result whose fine_card is still graph-exact,
 * or null when no model is ready / generation fails (caller uses Tier 3).
 */
export async function narrateWithSLM(
  session: OfflineSession,
  text: string,
): Promise<OfflineResult | null> {
  if (!onDeviceLLMReady() || !_model) return null;
  try {
    const context = buildRagContext(session, text);
    const prompt = `${SYSTEM}\n\n${context}\n\nUser: ${text}\nDriveLegal:`;
    const reply = (await _model.generate(prompt, { maxTokens: 200, temperature: 0.3 })).trim();
    if (!reply) return null;

    // Ground the structured card in the graph regardless of what the model said.
    const det = narrate(session, text); // reuse deterministic resolution for the code/card
    return {
      reply,
      card: det.card,
      needs: det.needs,
      violationCode: det.violationCode,
      intent: 'narrate_slm',
    };
  } catch {
    return null; // any runtime error → deterministic fallback
  }
}
