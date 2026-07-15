/**
 * offlineEngine.ts — on-device port of backend/offline_engine.py
 * ──────────────────────────────────────────────────────────────
 * Rich rule-based narrate handler that runs entirely on the device (no server,
 * no model). Produces the same kind of reply the server's offline_engine does,
 * grounded in the local graph so fine amounts are always exact.
 */

import { FineCard, getState, getViolation, quickFine } from './graph';
import { isDeterministic, resolve } from './resolver';

export interface OfflineSession {
  state_code?: string | null;
  city_code?: string | null;
  city_name?: string | null;
  road_bucket?: string | null;
  vehicle_segment?: string | null;
  vehicle_fine_class?: string | null;
  vehicle_type?: string | null;
  violation_code?: string | null;
}

export interface OfflineResult {
  reply: string;
  card: (FineCard & { section: string | null }) | null;
  needs: string;
  violationCode: string | null;
  intent: string;
}

const GREET = /^\s*(hi|hello|hey|namaste|namaskar|hola|good\s+(morning|afternoon|evening|day)|howdy|sup|what'?s up)\b/i;
const BYE = /^\s*(bye|goodbye|ok thanks?|thanks?|thank you|ok|okay|got it|alright|great)\b\s*$/i;
const DOC_RE = /\b(rc|registration|dl|driving licen[cs]e|insurance|puc|pollution|emission|fitness)\b/i;
const LIC_RE = /\b(learner|learners|ll|how (to|do i) get|apply for|minimum age|parivahan)\s*(licen[cs]e|dl|driving)?\b/i;
const ACC_RE = /\b(accident|crash|collision|hit (and|&) run|pedestrian.*hit|injured|bleeding|what (do|should) i do)\b/i;
const SPEED_RE = /\b(speed limit|how fast|maximum speed|kmph|km\/h)\b/i;
const GENERAL_RE = /\b(what are|tell me|explain|rules|laws|penalties|common (fines|violations)|what (happens|is the fine))\b/i;

const GREET_REPLIES = [
  "Namaste! 🙏 I'm DriveLegal — your offline traffic law assistant. Ask me about any fine, traffic rule, or road law in India and I'll give you the exact answer straight from the Motor Vehicles Act.",
  "Hello! I'm DriveLegal. Even without internet, I have the complete Indian traffic law dataset ready. What would you like to know — a fine amount, a rule, or something else?",
  "Hey there! DriveLegal here — fully offline and ready. What traffic law question can I help with?",
];
const BYE_REPLIES = [
  'Drive safe out there! 🙏 Come back anytime you need traffic law guidance.',
  'Glad I could help! Stay safe on the roads. 🚦',
  "You're welcome! Remember — most fines are avoidable with small habits. Safe driving!",
];

const DOC_FACTS: Record<string, string> = {
  dl: "**Driving Licence (MV Act §3, §9)** — carry the original or a DigiLocker digital copy; both are legally valid. Driving **without a valid DL**: ₹5,000 fine (first offence), up to ₹10,000 on repeat + possible vehicle seizure. Renew online at parivahan.gov.in before expiry to avoid the late fee.",
  rc: "**RC — Registration Certificate (MV Act §39)** — your vehicle's identity document. Must be carried (original or DigiLocker copy). Driving **without RC**: ₹5,000 fine + possible vehicle seizure.",
  insurance: "**Third-party insurance (MV Act §146)** — MANDATORY. Driving **without valid insurance**: ₹2,000 fine + up to 3 months imprisonment. Keep a soft copy in DigiLocker.",
  puc: "**PUC — Pollution Under Control (CMVR Rule 115)** — required for all vehicles. Driving **without valid PUC**: ₹10,000 fine. Done at any authorized centre in ~5 minutes for ₹50–₹100.",
};

const ACCIDENT_STEPS = [
  'Stop immediately and switch on hazard lights — do NOT drive away (MV Act §134).',
  'Check on anyone injured. If serious: call **112** (emergency) or **108** (ambulance) right away.',
  'Exchange name, phone, RC number, and insurance details. Take photos of vehicles, damage, and the scene.',
  'Report at the nearest police station within 24 hours (MV Act §134) — a written FIR protects you legally.',
  'Inform your insurance company as soon as possible to start the claim.',
];
const LICENSE_STEPS = [
  "Apply for a **Learner's Licence** at parivahan.gov.in or your RTO. Min age: 16 (gearless ≤50cc), 18 (car / other two-wheelers), 20 (transport).",
  'Pass the online **theory test** — road signs and basic rules (free, ~15 min).',
  'Wait **at least 30 days** after the LL before applying for the permanent DL.',
  'Book a **driving test slot** at your RTO. On passing, the permanent DL is issued.',
  "While on an LL, display the **'L' plate** and keep a licensed driver beside you.",
];

const ASK_STATE = 'To get the exact fine I need to know **which state** you\'re in — fines under the MV Act vary by state. Just tell me your state.';
const ASK_VEHICLE = 'Got it! **What type of vehicle** were you on — bike/scooter, car, auto, or something else? The fine can differ by vehicle class.';
const UNCLEAR = 'I want to give you accurate information. Could you tell me a bit more — which violation, which state, and what vehicle? I have the complete Indian traffic law database ready offline.';

const SEGMENT_LABEL: Record<string, string> = {
  two_wheeler: 'two-wheeler',
  three_wheeler: 'auto / three-wheeler',
  four_wheeler: 'car',
  four_wheeler_plus: 'bus / heavy vehicle',
  special: 'special vehicle',
};

function pick<T>(arr: T[]): T { return arr[Math.floor(Math.random() * arr.length)]; }

function locStr(s: OfflineSession): string {
  if (s.city_name) return s.city_name;
  if (s.state_code) return getState(s.state_code)?.name || s.state_code;
  return 'your area';
}
function vehLabel(s: OfflineSession): string {
  return s.vehicle_type || (s.vehicle_segment ? SEGMENT_LABEL[s.vehicle_segment] : '') || 'your vehicle';
}
function fmtFine(a: number | null | undefined): string {
  return a == null ? 'as per schedule' : `₹${a.toLocaleString('en-IN')}`;
}

function buildCard(s: OfflineSession, vioCode: string): (FineCard & { section: string | null }) | null {
  const c = quickFine(vioCode, s.state_code, s.city_code, s.vehicle_fine_class);
  if (!c) return null;
  return { ...c, section: c.mv_section, city_name: s.city_name ?? null } as any;
}

function buildFineResponse(s: OfflineSession, vioCode: string): string {
  const vio = getViolation(vioCode);
  const card = quickFine(vioCode, s.state_code, s.city_code, s.vehicle_fine_class);
  const name = vio?.name || vioCode;
  const sec = vio?.mv_section;
  const secStr = sec ? ` (MV Act §${sec})` : '';
  const loc = locStr(s);
  const veh = vehLabel(s);
  const parts: string[] = [];

  if (card && card.fine_first) {
    let lead = `For **${name}**${secStr} in **${loc}** (${veh}), the first-offence fine is **${fmtFine(card.fine_first)}** — ${card.fine_source} schedule.`;
    if (card.fine_repeat) lead += ` Repeat offence: **${fmtFine(card.fine_repeat)}**.`;
    parts.push(lead);
  } else {
    parts.push(`**${name}**${secStr} is an offence under the Motor Vehicles Act. I couldn't pinpoint the exact amount for ${loc}/${veh} — the central MV Act schedule applies.`);
  }

  const details: string[] = [];
  if (card?.imprisonment) details.push(`Imprisonment: up to ${card.imprisonment}.`);
  details.push(vio?.compoundable
    ? '✅ Compoundable — can be paid on-the-spot to the officer.'
    : '❌ NOT compoundable — requires a court appearance.');
  parts.push(details.join(' '));

  const conseq = vio?.dl_consequence || vio?.consequence;
  if (conseq) parts.push(`🪪 **Licence impact**: ${conseq}`);
  if (vio?.tips_to_avoid) parts.push(`💡 **Tip to avoid it**: ${vio.tips_to_avoid}`);
  if (vio?.what_to_do_next) parts.push(`📋 **If you're stopped**: ${vio.what_to_do_next}`);
  if (vio?.common_misconception) parts.push(`❌ **Common myth**: ${vio.common_misconception}`);
  return parts.join('\n\n');
}

/** Faithful port of offline_engine.narrate_protocol (reply + optional card). */
export function narrate(session: OfflineSession, userText: string): OfflineResult {
  const text = (userText || '').trim();
  const wrap = (reply: string, needs = 'none', vioCode: string | null = null, intent = 'chat'): OfflineResult =>
    ({ reply, card: null, needs, violationCode: vioCode, intent });

  if (GREET.test(text)) return wrap(pick(GREET_REPLIES));
  if (BYE.test(text)) return wrap(pick(BYE_REPLIES));

  if (ACC_RE.test(text)) {
    const steps = ACCIDENT_STEPS.map((st, i) => `${i + 1}. ${st}`).join('\n');
    return wrap(`Take a breath — here's exactly what to do right now:\n\n${steps}\n\n*Reference: MV Act §134, BNS §106 (if fatality involved).*`, 'none', null, 'incident');
  }

  if (DOC_RE.test(text)) {
    const t = text.toLowerCase();
    const keys: string[] = [];
    if (/\b(rc|registration certificate)\b/.test(t)) keys.push('rc');
    if (/\b(dl|driving licen[cs]e|licence|license)\b/.test(t)) keys.push('dl');
    if (t.includes('insurance')) keys.push('insurance');
    if (/\b(puc|pollution|emission)\b/.test(t)) keys.push('puc');
    const use = keys.length ? keys : Object.keys(DOC_FACTS);
    const chunks = use.map((k) => DOC_FACTS[k]).filter(Boolean);
    return wrap(`Here's what you need to know:\n\n${chunks.join('\n\n')}\n\n💡 Keep digital copies in **DigiLocker** — legally equivalent to originals at any checkpoint.`, 'none', null, 'documents');
  }

  if (LIC_RE.test(text)) {
    const steps = LICENSE_STEPS.map((st, i) => `${i + 1}. ${st}`).join('\n');
    return wrap(`Getting a driving licence in India is straightforward. Here are the steps:\n\n${steps}\n\n**Portal:** parivahan.gov.in/parivahansewa`, 'none', null, 'license');
  }

  if (SPEED_RE.test(text)) {
    return wrap('General Indian speed limits (MV Act / CMVR):\n  • Expressway: 120 km/h (car), 80 km/h (bus/truck)\n  • National/State highway: 100 km/h (car), 60 km/h (bus/truck)\n  • City roads: 70 km/h (car), 60 km/h (others)\n  • School/hospital zone: 25 km/h\n\nSpeeding penalty: ₹1,000–₹2,000 (first), ₹2,000–₹4,000 (repeat). Speed cameras (ANPR) are active on most expressways.', 'none', null, 'speed');
  }

  // Violation-specific fine query
  let vioCode: string | null = session.violation_code || null;
  if (!vioCode) {
    const ranked = resolve(text, session.road_bucket, session.vehicle_fine_class);
    if (ranked.length && isDeterministic(ranked)) vioCode = ranked[0][0];
    else if (ranked.length && ranked[0][1] >= 2) vioCode = ranked[0][0];
  }

  if (vioCode) {
    if (!session.state_code) {
      return { reply: `I found the rule for that violation. ${ASK_STATE}`, card: null, needs: 'state_code', violationCode: vioCode, intent: 'narrate' };
    }
    if (!session.vehicle_segment) {
      return { reply: `Got the violation — ${ASK_VEHICLE}`, card: null, needs: 'vehicle_segment', violationCode: vioCode, intent: 'narrate' };
    }
    return {
      reply: buildFineResponse(session, vioCode),
      card: buildCard(session, vioCode),
      needs: 'none',
      violationCode: vioCode,
      intent: 'narrate',
    };
  }

  // No clear violation
  if (GENERAL_RE.test(text) || text.length > 10) {
    let needs = 'none';
    if (!session.state_code) needs = 'state_code';
    else if (!session.vehicle_segment) needs = 'vehicle_segment';
    return wrap('I have the complete Indian traffic law dataset ready offline. Ask me about a specific fine (e.g. "fine for no helmet"), a rule, or a situation (e.g. "I had an accident") and I\'ll give you the exact answer.', needs, null, 'narrate');
  }
  return wrap(UNCLEAR, 'violation_code', null, 'narrate');
}
