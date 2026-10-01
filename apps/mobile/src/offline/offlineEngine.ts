/**
 * offlineEngine.ts — on-device port of backend/offline_engine.py
 * ──────────────────────────────────────────────────────────────
 * Rich rule-based narrate handler that runs entirely on the device (no server,
 * no model). Produces the same kind of reply the server's offline_engine does,
 * grounded in the local graph so fine amounts are always exact.
 */

import { FineCard, getState, getViolation, groundAmounts, quickFine } from './graph';
import { CAPABILITY_REPLY, FAQ, IDENTITY_REPLY, UNSAFE_RE, UNSAFE_REPLY } from './faq.generated';
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
const DOC_RE = /\b(documents?|papers|paperwork|rc|registration|dl|driving licen[cs]e|insurance|puc|pollution|emission|fitness)\b/i;
const LIC_RE = /\b(learner|learners|ll|how (to|do i) get|apply for|minimum age|parivahan)\s*(licen[cs]e|dl|driving)?\b/i;
const ACC_RE = /\b(accident|crash|collision|hit (and|&) run|pedestrian.*hit|injured|bleeding|what (do|should) i do)\b/i;
const SPEED_RE = /\b(speed limit|how fast|maximum speed|kmph|km\/h)\b/i;
const GENERAL_RE = /\b(what are|tell me|explain|rules|laws|penalties|common (fines|violations)|what (happens|is the fine))\b/i;

// Safety guardrail — generated from backend/nlu.py (single source of truth).
/** True when the text asks for bribery / forgery / evasion help. */
export function isUnsafeRequest(text: string): boolean {
  return UNSAFE_RE.test(text || '');
}

// Follow-ups about the last answer — port of backend followups.py.
const FOLLOWUP_TOPICS: [string, RegExp][] = [
  ['compoundable', /\b(compound\w*|pay\s+(it\s+)?on\s+the\s+spot|spot\s+fine|settle|court)\b/i],
  ['jail', /\b(jail|prison|imprison\w*|arrest\w*|custody|lock(ed)?\s+up)\b/i],
  ['repeat', /\b(repeat\w*|second\s+time|third\s+time|next\s+time|subsequent|(caught|fined|stopped|do\s+it|happens?|did\s+it)\s+again)\b/i],
  ['licence', /\b(licen[cs]e|dl|suspend\w*|cancel\w*|disqualif\w*|points?)\b/i],
  ['pay', /\b(pay\s+(it|this|that|the\s+(fine|challan))|how\s+(do|to|can)\s+i\s+pay|where\s+(do|can)\s+i\s+pay|payment|pay\s+online)\b/i],
  ['bail', /\b(bail|bailable|non[-\s]?bailable)\b/i],
  ['section', /\b(section|sec\.?|which\s+law|what\s+law|mv\s*act|legal\s+basis)\b/i],
  ['next', /\b(what\s+(should|do|can)\s+i\s+do|what\s+now|next\s+steps?|stopped|contest|dispute|appeal)\b/i],
  ['avoid', /\b(avoid|prevent|tips?|how\s+not\s+to)\b/i],
  ['amount', /\b(how\s+much|amount|cost|fine\s+again|total)\b/i],
];

function followupReply(s: OfflineSession, text: string): string | null {
  if (!s.violation_code || text.split(/\s+/).length > 14) return null;
  const topics = FOLLOWUP_TOPICS.filter(([, rx]) => rx.test(text)).map(([k]) => k);
  if (!topics.length) return null;
  const vio = getViolation(s.violation_code);
  const card = quickFine(s.violation_code, s.state_code, s.city_code, s.vehicle_fine_class);
  const name = vio?.name || s.violation_code;
  const sec = vio?.mv_section;
  const lines: string[] = [];
  for (const t of topics) {
    if (t === 'compoundable') {
      lines.push(vio?.compoundable
        ? "✅ Yes — it's **compoundable**: you can pay it on the spot or online via e-challan without going to court."
        : "❌ No — it's **not compoundable**. The challan goes to court and you'll need to appear (or pay through the virtual court).");
    } else if (t === 'jail') {
      lines.push(card?.imprisonment
        ? `⚖️ Imprisonment: **${card.imprisonment}**.`
        : "⚖️ No imprisonment is prescribed for this offence — it's a fine only.");
    } else if (t === 'repeat') {
      lines.push(card?.fine_repeat
        ? `🔁 Repeat offence: **${fmtFine(card.fine_repeat)}** (first offence: ${fmtFine(card.fine_first)}).`
        : `🔁 The schedule doesn't list a separate repeat amount — the same ${fmtFine(card?.fine_first)} applies, though officers may add licence action.`);
    } else if (t === 'licence') {
      const lc = vio?.dl_consequence || vio?.consequence;
      lines.push(lc ? `🪪 Licence impact: ${lc}`
        : '🪪 No mandatory licence suspension is listed for this offence, but repeat offences can be referred to the RTO.');
    } else if (t === 'pay') {
      lines.push('💳 Pay online at **echallan.parivahan.gov.in** (or your state traffic-police portal/app) using the challan number from the SMS — UPI, card and net banking work. If you pay on the spot, insist on the officer\'s e-challan device and a receipt.'
        + (vio?.compoundable ? '' : '\n\n⚠️ This offence isn\'t compoundable, so it may be listed for court — check the challan status before paying.'));
    } else if (t === 'bail') {
      lines.push(card?.imprisonment
        ? "🔓 Most Motor Vehicles Act offences are **bailable** — if you're arrested, bail can be granted at the police station or by the magistrate. Offences that involve causing death (e.g. BNS §106) are treated more seriously. A lawyer can confirm for your specific case."
        : "🔓 There's no arrest or jail for this offence — it's fine-only, so bail doesn't come into it.");
    } else if (t === 'section') {
      lines.push(sec ? `📖 It falls under **MV Act §${sec}**.` : "📖 The graph doesn't record a specific section for this one.");
    } else if (t === 'next') {
      lines.push(`${vio?.what_to_do_next ? `📋 ${vio.what_to_do_next}\n\n` : ''}You can pay or contest any e-challan at **echallan.parivahan.gov.in**.`);
    } else if (t === 'avoid') {
      if (vio?.tips_to_avoid) lines.push(`💡 ${vio.tips_to_avoid}`);
    } else if (t === 'amount') {
      lines.push(`💰 First offence: **${fmtFine(card?.fine_first)}**${card?.fine_repeat ? ` · Repeat: **${fmtFine(card.fine_repeat)}**` : ''} (${card?.fine_source || 'central'} schedule).`);
    }
  }
  if (!lines.length) return null;
  return `About **${name}**${sec ? ` (MV Act §${sec})` : ''}:\n\n${lines.join('\n\n')}`;
}

const FOLLOWUP_TERMS = /\b(suspend\w*|cancel\w*|disqualif\w*|jail|prison|imprison\w*|arrest\w*|court|compound\w*|repeat\w*|section|contest|appeal|pay|paid|receipt|points?)\b/gi;
const NEG_BEFORE_LICENCE = /\b(without|no|expired|fake|forgot|lost|don'?t\s+have|didn'?t\s+have|never\s+had)\s+(a\s+|my\s+|the\s+|driving\s+)*$/i;

export function maskFollowupTerms(text: string): string {
  const t = (text || '').replace(FOLLOWUP_TERMS, ' ');
  const out: string[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(t))) {
    const w = m[0];
    if (/^(licen[cs]e|dl)\W*$/i.test(w) && !NEG_BEFORE_LICENCE.test(t.slice(0, m.index))
        && !/^\s*(one|fine|offence|violation|challan|case|thing)\b/i.test(t.slice(m.index + w.length))) continue;
    out.push(w);
  }
  return out.join(' ');
}

// Vehicle detection — port of graph_engine.detect_vehicle_segment.
const VEHICLE_WORDS: [string, string][] = [
  ['bike', 'two_wheeler'], ['motorcycle', 'two_wheeler'], ['scooter', 'two_wheeler'], ['moped', 'two_wheeler'],
  ['two wheeler', 'two_wheeler'], ['two-wheeler', 'two_wheeler'], ['motorbike', 'two_wheeler'], ['scooty', 'two_wheeler'],
  ['auto', 'three_wheeler'], ['auto rickshaw', 'three_wheeler'], ['rickshaw', 'three_wheeler'], ['e-rickshaw', 'three_wheeler'],
  ['car', 'four_wheeler'], ['sedan', 'four_wheeler'], ['suv', 'four_wheeler'], ['hatchback', 'four_wheeler'],
  ['taxi', 'four_wheeler'], ['cab', 'four_wheeler'], ['jeep', 'four_wheeler'],
  ['truck', 'heavy_vehicle'], ['lorry', 'heavy_vehicle'], ['bus', 'four_wheeler_plus'], ['minibus', 'four_wheeler_plus'],
];
export const SEGMENT_FINE_CLASS: Record<string, string> = {
  two_wheeler: '2W', three_wheeler: '3W_PASS', four_wheeler: 'LMV', four_wheeler_plus: 'HPV', heavy_vehicle: 'HGV',
};

export function detectVehicleSegment(text: string, minScore = 1): string | null {
  const t = ` ${(text || '').toLowerCase()} `;
  let best: { score: number; pos: number; seg: string } | null = null;
  for (const [kw, seg] of VEHICLE_WORDS) {
    const re = new RegExp(`(?<![a-z0-9])${kw.replace(/[-]/g, '\\-')}s?(?![a-z0-9])`, 'g');
    let m: RegExpExecArray | null;
    while ((m = re.exec(t))) {
      if (kw === 'bus' && /^\s*(stop|stand|lane|depot|bay)/.test(t.slice(m.index + m[0].length))) continue;
      const before = t.slice(Math.max(0, m.index - 24), m.index);
      const score = /\b(my|our|on\s+(a|my|the)|in\s+(a|my|the)|riding|driving|drove|rode|was\s+on|his|her)\s+(own\s+)?$/.test(before) ? 3 : 1;
      if (!best || score > best.score || (score === best.score && m.index < best.pos)) best = { score, pos: m.index, seg };
    }
  }
  return best && best.score >= minScore ? best.seg : null;
}

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
const ASK_VEHICLE = 'One more thing — **what type of vehicle** were you on — bike/scooter, car, auto, or something else? The fine can differ by vehicle class.';
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
  return s.vehicle_type || (s.vehicle_segment ? SEGMENT_LABEL[s.vehicle_segment] : '') || '';
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
    let lead = `For **${name}**${secStr} in **${loc}**${veh ? ` (${veh})` : ''}, the first-offence fine is **${fmtFine(card.fine_first)}** — ${card.fine_source} schedule.`;
    if (card.fine_repeat) lead += ` Repeat offence: **${fmtFine(card.fine_repeat)}**.`;
    parts.push(lead);
  } else {
    parts.push(`**${name}**${secStr} is an offence under the Motor Vehicles Act. I couldn't pinpoint the exact amount for ${loc}${veh ? `/${veh}` : ''} — the central MV Act schedule applies.`);
  }

  const details: string[] = [];
  if (card?.imprisonment) {
    const imp = String(card.imprisonment).trim().replace(/\.$/, '');
    details.push(`Imprisonment: ${/^\d/.test(imp) ? 'up to ' : ''}${imp}.`);
  }
  details.push(vio?.compoundable
    ? '✅ Compoundable — can be paid on-the-spot to the officer.'
    : '❌ NOT compoundable — requires a court appearance.');
  parts.push(details.join(' '));

  // Grounded advice text from the card (clauses quoting other amounts dropped).
  const allowed = new Set<number>([card?.fine_first, card?.fine_repeat].filter((x): x is number => typeof x === 'number' && x > 0));
  const conseq = card ? card.licence_consequence : (vio?.dl_consequence || vio?.consequence);
  if (conseq) parts.push(`🪪 **Licence impact**: ${conseq}`);
  const tips = card ? card.tips_to_avoid : vio?.tips_to_avoid;
  const todo = card ? card.what_to_do_next : vio?.what_to_do_next;
  const myth = card ? groundAmounts(vio?.common_misconception, allowed) : vio?.common_misconception;
  if (tips) parts.push(`💡 **Tip to avoid it**: ${tips}`);
  if (todo) parts.push(`📋 **If you're stopped**: ${todo}`);
  if (myth) parts.push(`❌ **Common myth**: ${myth}`);
  return parts.join('\n\n');
}

/** Faithful port of offline_engine.narrate_protocol (reply + optional card). */
export function narrate(session: OfflineSession, userText: string): OfflineResult {
  const text = (userText || '').trim();
  const wrap = (reply: string, needs = 'none', vioCode: string | null = null, intent = 'chat'): OfflineResult =>
    ({ reply, card: null, needs, violationCode: vioCode, intent });

  if (GREET.test(text)) return wrap(pick(GREET_REPLIES));
  if (BYE.test(text)) return wrap(pick(BYE_REPLIES));
  if (isUnsafeRequest(text)) return wrap(UNSAFE_REPLY, 'none', null, 'guardrail');
  if (/\b(who\s+are\s+you|what\s+are\s+you|your\s+name)\b/i.test(text)) return wrap(IDENTITY_REPLY);
  if (/\b(what\s+can\s+you\s+do|how\s+can\s+you\s+help)\b/i.test(text)) return wrap(CAPABILITY_REPLY);
  // Grounded FAQ (generated from the server's nlu.py) — questions only, so a
  // story that mentions e.g. "towed" still reaches violation matching.
  if (/^\s*(what|how|is|are|can|could|do|does|will|would|should|why|when|where|which)\b/i.test(text) || text.trim().endsWith('?')) {
    for (const [rx, answer] of FAQ) if (rx.test(text)) return wrap(answer, 'none', null, 'info');
  }

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

  // Violation-specific fine query. A clearly named violation wins; otherwise a
  // short question about the previous answer ("is it compoundable?") is
  // answered from that violation; a weak keyword match comes last.
  let vioCode: string | null = null;
  // Consequence words ("can they suspend my licence?") mustn't select a new
  // offence — port of nlu.mask_followup_terms.
  const ranked = resolve(maskFollowupTerms(text), session.road_bucket, session.vehicle_fine_class);
  if (ranked.length && isDeterministic(ranked)) vioCode = ranked[0][0];
  if (!vioCode || vioCode === session.violation_code) {
    const fu = followupReply(session, text);
    if (fu) {
      return {
        reply: fu,
        card: session.violation_code ? buildCard(session, session.violation_code) : null,
        needs: 'none',
        violationCode: session.violation_code || null,
        intent: 'narrate',
      };
    }
  }
  if (!vioCode && ranked.length && ranked[0][1] >= 2) vioCode = ranked[0][0];

  if (vioCode) {
    if (!session.state_code) {
      return { reply: `I found the rule for that violation. ${ASK_STATE}`, card: null, needs: 'state_code', violationCode: vioCode, intent: 'narrate' };
    }
    // Infer the vehicle from the text, or from a rule that only exists for
    // two-wheelers (helmet / pillion / triple riding).
    const explicit = detectVehicleSegment(text, 3);   // "…on my car" overrides memory
    if (explicit && explicit !== session.vehicle_segment) {
      session.vehicle_segment = explicit;
      session.vehicle_fine_class = SEGMENT_FINE_CLASS[explicit] || null;
      session.vehicle_type = null;
    }
    if (!session.vehicle_segment) {
      const va = getViolation(vioCode)?.vehicle_applicability || ['ALL'];
      const seg = detectVehicleSegment(text) || (va.length === 1 && va[0] === '2W' ? 'two_wheeler' : null);
      if (seg) {
        session.vehicle_segment = seg;
        session.vehicle_fine_class = SEGMENT_FINE_CLASS[seg] || null;
      }
    }
    if (!session.vehicle_segment) {
      return { reply: `I found the rule for that. ${ASK_VEHICLE}`, card: null, needs: 'vehicle_segment', violationCode: vioCode, intent: 'narrate' };
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
