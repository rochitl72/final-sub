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
  'dont', 'don', 'didnt', 'didn', 'doesnt', 'doesn', 'wasnt', 'isnt', 'cant',
  'can', 'will', 'would', 'should', 'what', 'why', 'how', 'when', 'which',
]);

const ACTION_VERBS = new Set([
  'parked', 'driving', 'drove', 'driven', 'riding', 'rode', 'ridden',
  'went', 'going', 'gone', 'moving', 'moved', 'stopped', 'standing', 'waiting',
]);

// Words too generic for single-token overlap (port of _OVERLAP_NOISE).
const OVERLAP_NOISE = new Set([
  ...ACTION_VERBS,
  'while', 'ride', 'drive', 'bike', 'car', 'scooter', 'scooty', 'motorcycle',
  'road', 'person', 'people', 'one', 'time', 'caught', 'cop', 'cops', 'officer',
  'someone', 'something', 'said', 'told', 'today', 'yesterday',
]);

const PHRASE_GENERIC = new Set([
  'while', 'broke', 'break', 'working', 'work', 'give', 'gave', 'given', 'using', 'wearing',
  'took', 'take', 'taken', 'left', 'kept', 'keep', 'made', 'make', 'came', 'come', 'side',
  'road', 'time', 'near', 'after', 'before', 'front', 'back', 'with', 'have', 'been', 'there',
  'refused', 'refuse', 'refusing',
  'bike', 'scooter', 'scooty', 'motorcycle', 'truck', 'lorry', 'auto', 'vehicle', 'car', 'cars',
  'bikes', 'taxi',
]);

let _vtoks: Record<string, Set<string>> | null = null;
function violationTokens(): Record<string, Set<string>> {
  if (_vtoks) return _vtoks;
  const out: Record<string, Set<string>> = {};
  for (const [kw, vcodes] of Object.entries(violationsByKeyword())) {
    const toks = tokens(kw).filter((t) => !STOPWORDS.has(t) && t.length > 2);
    for (const vc of vcodes) { out[vc] = out[vc] || new Set(); toks.forEach((t) => out[vc].add(t)); }
  }
  _vtoks = out;
  return out;
}

function tokens(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

// ── Typo repair (port of violation_resolver.correct_typos) ──────────────────
const NO_CORRECT = new Set([
  'night', 'light', 'being', 'bring', 'while', 'where', 'there', 'their', 'these',
  'those', 'wrong', 'right', 'coming', 'going', 'doing', 'since', 'stole', 'still',
  'spent', 'sent', 'want', 'went', 'cops', 'copy', 'file', 'fire', 'hire', 'here',
]);

function edit1(a: string, b: string): boolean {
  if (a === b || Math.abs(a.length - b.length) > 1) return false;
  if (a.length === b.length) {
    const diff: number[] = [];
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) diff.push(i);
    if (diff.length === 1) return true;
    return diff.length === 2 && diff[1] === diff[0] + 1 && a[diff[0]] === b[diff[1]] && a[diff[1]] === b[diff[0]];
  }
  if (a.length > b.length) [a, b] = [b, a];
  let i = 0;
  while (i < a.length && a[i] === b[i]) i++;
  return a.slice(i) === b.slice(i + 1);
}

let _vocab: Set<string> | null = null;
function vocab(): Set<string> {
  if (_vocab) return _vocab;
  _vocab = new Set();
  for (const k of Object.keys(violationsByKeyword())) for (const t of tokens(k)) if (t.length >= 4) _vocab.add(t);
  return _vocab;
}

export function correctTypos(text: string): string {
  const v = vocab();
  let changed = false;
  const out = text.split(/(\W+)/).map((tok) => {
    const low = tok.toLowerCase();
    if (low.length >= 5 && /^[a-z]+$/.test(low) && !v.has(low) && !NO_CORRECT.has(low) && !STOPWORDS.has(low)) {
      const cands = [...v].filter((w) => w[0] === low[0] && edit1(low, w));
      if (cands.length === 1) { changed = true; return cands[0]; }
    }
    return tok;
  });
  return changed ? out.join('') : text;
}

export type Ranked = [string, number][];

/** Faithful port of ViolationResolver.resolve. */
export function resolve(
  text: string,
  roadBucket?: string | null,
  vehicleFineClass?: string | null,
): Ranked {
  if (!text) return [];
  text = correctTypos(text);
  const nText = norm(text);
  if (!nText) return [];
  const nPadded = ` ${nText} `;

  const kwIndex = violationsByKeyword();
  const scores: Record<string, number> = {};

  // 1. Exact / substring phrase hits
  for (const [kw, vcodes] of Object.entries(kwIndex)) {
    if (!kw || kw.length < 3) continue;
    if (nPadded.includes(kw) || kw === nText) {
      const whole = nPadded.includes(` ${kw} `) || kw === nText;
      // Inside-word hits only for long keywords at a word start ("helmets").
      if (!whole && (kw.length < 5 || !nPadded.includes(` ${kw}`))) continue;
      const bonus = whole ? 5 : 3;
      for (const vc of vcodes) scores[vc] = (scores[vc] ?? 0) + bonus;
    }
  }

  // 2. Per-token overlap
  const textTokens = new Set(tokens(text).filter((t) => !STOPWORDS.has(t) && !OVERLAP_NOISE.has(t) && t.length > 2));
  if (textTokens.size) {
    // Distinct overlapping tokens counted once per violation (port).
    for (const [vc, vtoks] of Object.entries(violationTokens())) {
      let overlap = 0;
      for (const t of textTokens) if (vtoks.has(t)) overlap++;
      if (overlap) scores[vc] = (scores[vc] ?? 0) + overlap;
    }
  }

  // 2.5 Phrase match (port): a 2–3 word phrase from the text that appears in
  // exactly ONE violation's keywords is a distinctive signal → +6.
  const toks = nText.split(' ');
  let phrases: string[] = [];
  for (let i = 0; i < toks.length - 1; i++) {
    phrases.push(`${toks[i]} ${toks[i + 1]}`);
    if (i < toks.length - 2) phrases.push(`${toks[i]} ${toks[i + 1]} ${toks[i + 2]}`);
  }
  phrases = phrases.filter((p) => p.split(' ').some((w) => w.length >= 4 && !STOPWORDS.has(w) && !ACTION_VERBS.has(w) && !PHRASE_GENERIC.has(w)));
  if (phrases.length) {
    const hits: Record<string, Set<string>> = {};
    for (const [kw, vcodes] of Object.entries(kwIndex)) {
      if (!kw || kw.length < 5) continue;
      for (const p of phrases) {
        if (kw.includes(p)) {
          hits[p] = hits[p] || new Set();
          for (const vc of vcodes) hits[p].add(vc);
        }
      }
    }
    const boost = new Set<string>();
    for (const codes of Object.values(hits)) if (codes.size === 1) codes.forEach((c) => boost.add(c));
    for (const vc of boost) scores[vc] = (scores[vc] ?? 0) + 6;
  }

  if (Object.keys(scores).length === 0) return [];

  // 3. Bucket + vehicle relevance bonuses
  const bucketSet = new Set(roadBucket ? vioByBucket(roadBucket) : []);
  for (const vc of Object.keys(scores)) {
    const nid = `vio:${vc}`;
    if (scores[vc] < 3) continue; // bonuses refine real matches only
    if (roadBucket && bucketSet.has(nid)) scores[vc] += 2;
    if (vehicleFineClass) {
      const node = getViolation(vc);
      const va = (node && node.vehicle_applicability) || ['ALL'];
      if ((va.length === 1 && va[0] === 'ALL') || va.includes(vehicleFineClass)) {
        scores[vc] += 1;
      }
    }
  }

  // 4. Near-duplicate rules that differ in WHO / WHAT (port of _WHO_DISAMBIG
  //    + stated-age rules in violation_resolver.resolve).
  const prefer = (code: string, losers: string[]) => {
    const top = Math.max(scores[code] ?? 0, ...losers.map((l) => scores[l] ?? 0)) + 5;
    scores[code] = top;
    for (const l of losers) if (l in scores) scores[l] = Math.min(scores[l], Math.floor(top / 2));
  };
  for (const [topic, cue, pref, over] of WHO_DISAMBIG) {
    if ((pref in scores || over in scores) && topic.test(text) && cue.test(text)) prefer(pref, [over]);
  }
  const am = text.match(/\b(\d{1,2})\s*-?\s*(?:years?|yrs?)\s*-?\s*old\b/i);
  if (am) {
    const age = parseInt(am[1], 10);
    if (age <= 4 && /\b(bike|scooter|scooty|two[-\s]?wheeler|helmet)\b/i.test(text)) {
      prefer('SAFETY_NO_CHILD_2W', ['SAFETY_NO_HELMET_RIDER', 'SAFETY_NO_HELMET_PILLION']);
    } else if (age >= 5 && age < 18 && /\b(driv\w*|drove|rode|rid(e|es|ing)|riding)\b/i.test(text)) {
      prefer('DOC_UNDERAGE', Object.keys(scores).filter((c) => c !== 'DOC_UNDERAGE' && c !== 'JUV_MINOR_DRIVING'));
    }
  }

  return Object.entries(scores).sort((a, b) => b[1] - a[1]);
}

const WHO_DISAMBIG: [RegExp, RegExp, string, string][] = [
  [/\bhelmet/i,
   /\b(pillion|passenger|co-?rider|back\s*seat|(on|at|in)\s+the\s+back|sitting\s+(behind|at\s+the\s+back)|behind\s+me|riding\s+behind)\b/i,
   'SAFETY_NO_HELMET_PILLION', 'SAFETY_NO_HELMET_RIDER'],
  [/\bseat\s*-?belt/i, /\b(back\s*seat|rear|passenger|behind)\b/i,
   'SAFETY_NO_SEATBELT_PASSENGER', 'SAFETY_NO_SEATBELT_DRIVER'],
  [/\bbreath|\bblow\b|\bbreathaly/i, /\brefus/i, 'IMPAIRED_REFUSE_TEST', 'IMPAIRED_DRUNK'],
];

/** Faithful port of ViolationResolver.is_deterministic. */
export function isDeterministic(ranked: Ranked): boolean {
  if (!ranked.length) return false;
  if (ranked.length === 1) return ranked[0][1] >= 3;
  const top = ranked[0][1];
  const runner = ranked[1][1];
  return top >= 3 && top >= 2 * Math.max(runner, 1);
}
