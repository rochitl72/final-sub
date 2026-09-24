# DriveLegal — Dataset Validation & Source Linking

**Task A of the production-readiness review.**
Repository: `rochitl72/final-sub` · Artifact audited: `data/compiled/drivelegal_graph.json`
Audit date: 22 September 2026 · Method: reproducible, scripted, re-runnable

---

## 1. The headline

The graph contains **2,443 nodes and 4,701 edges**, which I decomposed into **6,842 atomic,
individually-checkable claims**. Each claim was matched against retrieved primary law or an
authoritative reference.

| Verdict | Claims | Share |
|---|---:|---:|
| verified | 4,082 | 59.7% |
| unverified — no reachable source | 1,821 | 26.6% |
| **contradicted by the source** | **862** | **12.6%** |
| no legal citation at all | 54 | 0.8% |
| cited section is about something else | 21 | 0.3% |
| cited section does not exist | 2 | 0.0% |

**That 59.7% is misleading and you should not quote it.** It is inflated by 2,379 helpline
claims (mostly the national numbers 112/108/1033 repeated across 782 districts) and 846 RTO
codes. Strip those mechanical wins away and look at the part a legal chatbot actually answers
from — fines, sections, compoundability, speed limits, rules:

> ### Of the 1,799 legal and rule claims, **305 are verified — 17.0%**.
> **104 are contradicted by the statute.** The rest have no traceable source.

---

## 2. The single most important finding

**The graph has zero provenance.** Across 2,443 nodes and 4,701 edges there is not one
`source`, `url`, `citation`, `gazette`, `notification_no`, or `retrieved_at` field. The
compiled JSON was committed in a single commit (`f686ec0`) with no builder script and no raw
scraped inputs anywhere in git history — so nothing can be traced back through the repository.
Every source link in this audit had to be re-established from outside the project.

For a product whose premise is *telling people what the law says*, that is the defect that
matters most. It is now fixed structurally: see §6.

---

## 3. Contradicted central fines — verified against the statute

These were checked against the Motor Vehicles Act 1988 **as amended by the MV (Amendment) Act
2019**, cross-checked against the Gazette text of the amending Act itself.

### 3.1 The §177A cluster — 13 of the most common violations, all wrong

Section 177A reads, in full:

> *"Whoever contravenes the regulations made under section 118, shall be punishable with fine
> which shall not be less than five hundred rupees, but may extend to one thousand rupees."*

The statutory range is **₹500–₹1,000**. The graph assigns **₹1,500** to every one of:

no-entry zone · prohibited U-turn · one-way wrong direction · stopping on a zebra crossing ·
failing to give way to a pedestrian · lane indiscipline · bus/taxi-lane misuse · parking on a
footpath · failure to yield right of way · disobeying a mandatory sign · driving with headphones ·
no turn-indicator on an expressway · non-EV in an EV bay

These are the everyday violations a user is most likely to ask about. Every answer is wrong,
and wrong in the direction of overstating the penalty by 50–200%.

### 3.2 Other central contradictions

| Violation | Cited | Graph says | Statute says |
|---|---|---:|---|
| Overloading of passenger vehicle | §194A | ₹1,000 flat | **₹200 *per excess passenger*** — wrong amount *and* wrong structure |
| Abandoned vehicle on road | §201 | ₹50 | **up to ₹500** (₹50 is the pre-2019 figure) |
| Exceeding speed limit by >50% | §183(2) | ₹5,000 | **₹1,000–2,000 (LMV) / ₹2,000–4,000 (MGV, HGV)** |
| Fake / copied number plate | §177 | ₹5,000 | §177 fixes **₹500 / ₹1,500** |
| Driving a vehicle in unsafe condition | §189 | ₹1,500 | §189 is *Racing and trials of speed* (₹5,000/₹10,000). The correct section is §190 |
| Leaving the scene of an accident | §161 | ₹5,000 | §161 is the hit-and-run *compensation* provision, not a penalty |

### 3.3 Structural legal problems

- **27 violations carry no MV Act section at all** — presented as enforceable offences with no
  legal authority whatsoever (e.g. *"Charging EV at unauthorised outlet in public"*,
  *"Parking in disabled-reserved spot without permit"*, *"Driving below minimum prescribed
  speed"*).
- **36 compoundability flags contradict §200.** §200's list is closed and does not include
  §177A, so the entire §177A cluster is marked compoundable when the statute does not provide
  for it. (Caveat: several states do compound these administratively; the *statutory* position
  is as stated.)
- **21 citations point at a section about something else**, and 2 cite sections that do not
  exist in the Act.
- Many citations are not citations at all: `"NHAI rules r/w CMVR"`, `"122 / municipal
  bye-laws"`, `"52 (MoRTH advisory 2017)"`, `"93 r/w Motor Vehicle Aggregator Guidelines
  2020/2025"`. These cannot be resolved to any instrument.

---

## 4. The state and city fine layer has no source — and shows signs of derivation

**All 693 subnational fine amounts (590 state, 103 city) are unverified.** Under §200 MV Act,
compounding amounts below the central level are fixed by each State Government by notification
in its Official Gazette. I probed **44 official portals** — every domain the repo's own
`gov_scraper.py` targets, plus state transport departments and city traffic-police sites.

> **9 of 44 responded. None published a machine-readable fine schedule.**

Full evidence in `source_reachability.json`. Two results deserve separate mention because they
also affect Task B:

- `parivahan.gov.in/parivahan//en/content/traffic-fines-challan` — the URL the entire update
  pipeline is anchored to — **returns a genuine HTTP 404**. The server answered; the page is
  gone.
- `morth.nic.in` is now a single-page app that serves the same 41 KB HTML shell for *every*
  path, including `.pdf` URLs. Any scraper pointed at it silently receives markup instead of
  documents and cannot tell the difference.

**Internal-structure evidence.** Of 435 state fine rows comparable to a central row:

- **269 (62%) are byte-identical to the central amount** — they carry no state-specific
  information at all.
- The remainder cluster on suspiciously round fractions: ×0.5 (40 rows), ×0.2 (51), ×0.1 (29),
  ×2.0 (16), ×0.4, ×0.3.
- The per-state `multiplier` field (values 0.1 / 0.9 / 1.0) reproduces the actual
  state÷central ratio in only **257 of 435 rows**, so it is not even internally consistent with
  the fines it supposedly generates.

This pattern is consistent with amounts being *derived* rather than *transcribed from
gazettes*. I cannot prove that, and I have not marked them contradicted — but a claim with no
source that also looks generated should not be served to users as law.

**The `multiplier` field itself is marked contradicted.** §200 lets a State Government specify
compounding amounts by notification; it provides no mechanism for scaling central fines by a
coefficient. A per-state multiplier is a modelling device, not a legal quantity.

---

## 5. Geography, jurisdiction and the rest

**The district helpline layer is systematically misattributed — 755 contradicted claims.**
`traffic_helpline` has only **37 distinct values across 790 districts**. State-level
control-room numbers have been copied onto every district in the state and presented as
district-specific. A user in a small district is given a number that is not their district's.

| Layer | Result |
|---|---|
| Coordinates (609) | 518 verified against the GeoNames India gazetteer; 89 unresolvable by name; 2 flagged for review. `Paschim Bardhaman` looks genuinely wrong — stored at Bardhaman town (Purba Bardhaman) rather than Asansol, its headquarters. |
| RTO codes (912) | 846 confirmed to be real Indian RTO codes. **This does not confirm they map to the stored district** — that needs each state transport department's own list. 66 not found. |
| RTO prefixes (36) | 34 verified. **Telangana is stored as `TS`; the current series is `TG`** (changed 2024). Codes already issued under `TS` remain valid, so the chatbot must handle both. Andaman & Nicobar could not be checked. |
| Speed limits (57) | 22 lawful under the MoRTH notification of 6 Apr 2018; **LCV on expressway is stored at 100 km/h against a national ceiling of 80**. 34 relate to state highways and district roads whose limits are state-notified and unreachable. Note: "lawful" ≠ "actually notified" — being under the ceiling does not mean any authority has fixed that number. |
| Vehicle classes (54) | 21 verified against CMVR; 33 depend on CMVR schedules and MoRTH category notifications not in the corpus. |
| City rules & facts (616) | **All unverified.** No official source for any of the 30 enriched cities was reachable, and the graph records none. |
| Corridors (132) | All structurally valid and inside India, but hand-drawn polylines with no linear reference. **Too coarse to decide which road a user is on** — a safety-relevant limitation, since road class drives the speed limit and applicable rules. |

---

## 6. What was produced

Nothing in the dataset was altered. Verified by automated diff: **0 nodes had a stored value
changed, added, or removed**; edges and indexes are byte-identical.

| File | What it is |
|---|---|
| `drivelegal_graph.v4.json` | The graph with a `_verification` block on every node — per-field status, confidence, source URL, retrieval timestamp, evidence quote, and reasoning — plus a top-level `sources` registry. |
| `ledger.json` | All 2,760 claims that are not verified, ordered by severity. This is the work queue. |
| `source_reachability.json` | Probe evidence for all 44 official portals, with HTTP status and timestamps. |
| `claims.json` | All 6,842 extracted claims with stable IDs. |
| `pipeline/` | The extractor, parsers and three verifiers. Re-runnable; the audit reproduces from scratch. |

### How the chatbot should consume this

`_verification.status` on each node gives a worst-case flag; `_verification.fields` gives it
per field. The minimum safe behaviour:

- **contradicted / off_topic / unsupported_section / no_citation** → do not serve the value.
- **unverified** → serve only with an explicit hedge and never as a specific rupee figure.
- **verified** → serve with the citation and retrieval date attached.

This turns the dataset from an unfalsifiable assertion into something a judge — or a user who
gets a real challan — can check.

---

## 7. Honest limits of this audit

- **Reachability is from a sandboxed egress.** Many `.gov.in` domains that did not respond here
  may well respond from within India. "Unverified" means *I could not source it*, not *no source
  exists*. `parivahan`'s 404 and MoRTH's SPA behaviour are exceptions — those are real defects
  observed from a server that answered.
- The consolidated Act text came from Indian Kanoon because `indiacode.nic.in` and
  `egazette.nic.in` were unreachable. I cross-checked its penalty amounts against the PRS copy
  of the 2019 Gazette and they agree. Note that `devgan.in`, a commonly used mirror, still
  serves **pre-2019** amounts and would have produced confident false verifications.
- Topical matching of a violation to a section uses keyword overlap. I special-cased §177 and
  §177A, which punish contravention of *other* instruments and so never name the conduct. Other
  cross-referencing sections may still be mis-scored — the 21 `off_topic` verdicts deserve a
  human read.
- RTO-code and coordinate verification confirm *existence*, not *mapping*. Both are labelled
  accordingly in the ledger rather than overclaimed.
- Reference-grade sources (Wikipedia for RTO codes and speed limits) are marked as such and
  carry lower confidence than statute.

---

## 8. Recommended next actions on Task A

1. **Fix the §177A cluster first** — 13 violations, one amount, highest user-facing impact.
2. **Delete or gate the 27 uncited violations.** They cannot be defended if challenged.
3. **Decide what the state fine layer is.** Either source it from gazettes properly, or stop
   presenting derived numbers as state law and fall back to the central figure with a clear
   "your state may compound this differently" caveat.
4. **Collapse the district helpline field.** Store it once at state level and say so, rather
   than 790 times as if it were district-specific.
5. **Make provenance mandatory.** Add a CI check that rejects any node carrying a rupee amount
   or a section citation without a `source_url` and `retrieved_at`.

Item 5 is the one that keeps this from happening again.
