#!/usr/bin/env python3
"""Fetch and register primary sources into the corpus with hashes + timestamps."""
import json, hashlib, re, html, subprocess, datetime, sys
from pathlib import Path

CORPUS = Path(__file__).parent / "corpus"
CORPUS.mkdir(exist_ok=True)
REG = CORPUS / "registry.json"
registry = json.loads(REG.read_text()) if REG.exists() else {}


def clean_html(raw: str) -> str:
    t = re.sub(r"<(script|style|noscript).*?</\1>", " ", raw, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", t, flags=re.I)
    t = re.sub(r"</t[dh]>", " | ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def fetch(key, url, title, authority, kind="html", timeout=90):
    """authority: primary_statute | gazette | govt_portal | state_govt | city_govt | reference"""
    dest = CORPUS / f"{key}.txt"
    if dest.exists() and registry.get(key, {}).get("ok"):
        print(f"  skip {key} (cached)")
        return registry[key]
    tmp = CORPUS / f"_{key}.raw"
    r = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", str(timeout), "-o", str(tmp),
         "-w", "%{http_code}", url],
        capture_output=True, text=True)
    code = (r.stdout or "").strip()[-3:]
    entry = {"key": key, "url": url, "title": title, "authority": authority,
             "http": code, "retrieved_at": datetime.datetime.now(datetime.timezone.utc)
             .isoformat(timespec="seconds"), "ok": False}
    if code != "200" or not tmp.exists() or tmp.stat().st_size < 400:
        entry["error"] = f"http {code}, size {tmp.stat().st_size if tmp.exists() else 0}"
        registry[key] = entry
        print(f"  FAIL {key}: {entry['error']}")
        tmp.unlink(missing_ok=True)
        return entry
    raw = tmp.read_bytes()
    if kind == "pdf" or raw[:4] == b"%PDF":
        pdf = CORPUS / f"{key}.pdf"
        pdf.write_bytes(raw)
        subprocess.run(["pdftotext", "-layout", str(pdf), str(dest)], capture_output=True)
        text = dest.read_text(errors="replace") if dest.exists() else ""
    else:
        text = clean_html(raw.decode("utf-8", errors="replace"))
        dest.write_text(text)
    tmp.unlink(missing_ok=True)
    entry.update(ok=len(text) > 400, chars=len(text),
                 sha256=hashlib.sha256(raw).hexdigest())
    if not entry["ok"]:
        entry["error"] = f"only {len(text)} chars of text"
    registry[key] = entry
    print(f"  {'OK  ' if entry['ok'] else 'THIN'} {key}: {len(text)} chars")
    return entry


SOURCES = [
    # --- central statute layer -------------------------------------------
    ("mva_1988_consolidated",
     "https://indiankanoon.org/doc/785258/",
     "The Motor Vehicles Act, 1988 (consolidated, as amended)",
     "primary_statute", "html"),
    ("cmvr_1989",
     "https://indiankanoon.org/doc/28072254/",
     "The Central Motor Vehicles Rules, 1989 (consolidated)",
     "primary_statute", "html"),
    ("mva_amendment_2019",
     "https://prsindia.org/files/bills_acts/bills_parliament/2019/"
     "Motor%20Vehicles%20(Amendment)%20Act,%202019.pdf",
     "The Motor Vehicles (Amendment) Act, 2019 (Act 32 of 2019), Gazette of India",
     "gazette", "pdf"),
]

if __name__ == "__main__":
    print("Fetching central statute layer...")
    for key, url, title, auth, kind in SOURCES:
        fetch(key, url, title, auth, kind)
    REG.write_text(json.dumps(registry, indent=1))
    print(f"\nregistry: {sum(1 for v in registry.values() if v.get('ok'))}/{len(registry)} ok")
