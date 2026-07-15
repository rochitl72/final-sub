/**
 * resolver.ts — on-device port of backend/violation_resolver.py
 * ─────────────────────────────────────────────────────────────
 * Deterministic keyword → violation_code resolver. Same scoring as the server
 * so an offline match ranks identically to an online one.
 */

import { getViolation, norm, vioByBucket, violationsByKeyword } from './graph';

// Same stopword list as violation_resolver._STOPWORDS ("without" intentionally
// kept high-signal; "with" kept as a stopword).
const STOPWORDS = new Set([
  'a', 'an', 'and', 'the', 'of', 'or', 'to', 'in', 'on', 'at', 'for',
  'with', 'is', 'was', 'were', 'be', 'by', 'from', 'i', 'my',
  'me', 'we', 'you', 'your', 'any', 'no', 'not', 'do', 'did', 'but',
  'this', 'that', 'these', 'those', 'as', 'if', 'so', 'it', 'its',
  'got', 'get', 'gotten', 'have', 'has', 'had', 'vehicle', 'vehicles',
  'driver', 'drivers', 'police', 'fine', 'fines', 'penalty',
]);

function tokens(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

export type Ranked = [string, number][];

/** Faithful port of ViolationResolver.resolve. */
export function resolve(
  text: string,
  roadBucket?: string | null,
  vehicleFineClass?: string | null,
): Ranked {
  if (!text) return [];
  const nText = norm(text);
  if (!nText) return [];
  const nPadded = ` ${nText} `;

  const kwIndex = violationsByKeyword();
  const scores: Record<string, number> = {};

  // 1. Exact / substring phrase hits
  for (const [kw, vcodes] of Object.entries(kwIndex)) {
    if (!kw || kw.length < 3) continue;
    if (nPadded.includes(kw) || kw === nText) {
      const bonus = (nPadded.includes(` ${kw} `) || kw === nText) ? 5 : 3;
      for (const vc of vcodes) scores[vc] = (scores[vc] ?? 0) + bonus;
    }
  }

  // 2. Per-token overlap
  const textTokens = new Set(tokens(text).filter((t) => !STOPWORDS.has(t) && t.length > 2));
  if (textTokens.size) {
    for (const [kw, vcodes] of Object.entries(kwIndex)) {
      const kwTokens = new Set(tokens(kw).filter((t) => !STOPWORDS.has(t) && t.length > 2));
      if (!kwTokens.size) continue;
      let overlap = 0;
      for (const t of textTokens) if (kwTokens.has(t)) overlap++;
      if (overlap) {
        for (const vc of vcodes) scores[vc] = (scores[vc] ?? 0) + overlap;
      }
    }
  }

  if (Object.keys(scores).length === 0) return [];

  // 3. Bucket + vehicle relevance bonuses
  const bucketSet = new Set(roadBucket ? vioByBucket(roadBucket) : []);
  for (const vc of Object.keys(scores)) {
    const nid = `vio:${vc}`;
    if (roadBucket && bucketSet.has(nid)) scores[vc] += 2;
    if (vehicleFineClass) {
      const node = getViolation(vc);
      const va = (node && node.vehicle_applicability) || ['ALL'];
      if ((va.length === 1 && va[0] === 'ALL') || va.includes(vehicleFineClass)) {
        scores[vc] += 1;
      }
    }
  }

  return Object.entries(scores).sort((a, b) => b[1] - a[1]);
}

/** Faithful port of ViolationResolver.is_deterministic. */
export function isDeterministic(ranked: Ranked): boolean {
  if (!ranked.length) return false;
  if (ranked.length === 1) return ranked[0][1] >= 3;
  const top = ranked[0][1];
  const runner = ranked[1][1];
  return top >= 3 && top >= 2 * Math.max(runner, 1);
}
