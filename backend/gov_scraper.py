#!/usr/bin/env python3
"""
gov_scraper.py — Official Indian government website scraper
===========================================================
STRICT RULES:
  • Only fetches URLs from the hardcoded GOV_SOURCES list.
  • Every URL is domain-checked against _ALLOWED_DOMAINS before any network call.
  • Uses SHA-256 content hashing — skips pages that have not changed.
  • Extracts only fine/penalty relevant text (strips nav, scripts, footers).
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from patch_engine import get_page_hash, set_page_hash

log = logging.getLogger("drivelegal.scraper")

# ── Verified .gov.in source list ──────────────────────────────────────────────

GOV_SOURCES: List[Dict] = [
    # ── Central / MV Act ─────────────────────────────────────────────────────
    {
        "state_code": None,
        "label":      "MoRTH Parivahan Traffic Fines",
        "url":        "https://parivahan.gov.in/parivahan//en/content/traffic-fines-challan",
        "domain":     "parivahan.gov.in",
    },
    {
        "state_code": None,
        "label":      "MoRTH Road Transport",
        "url":        "https://morth.nic.in/road-transport",
        "domain":     "morth.nic.in",
    },
    {
        "state_code": None,
        "label":      "NIC eGazette (MV Act notifications)",
        "url":        "https://egazette.nic.in/SearchEgazette.aspx?orgname=Motor+Vehicles",
        "domain":     "egazette.nic.in",
    },

    # ── State Traffic Police ──────────────────────────────────────────────────
    {
        "state_code": "DL",
        "label":      "Delhi Traffic Police — Fines",
        "url":        "https://delhitrafficpolice.nic.in/traffic-fines/",
        "domain":     "delhitrafficpolice.nic.in",
    },
    {
        "state_code": "MH",
        "label":      "Maharashtra Traffic Police",
        "url":        "https://mahatrafficpolice.gov.in",
        "domain":     "mahatrafficpolice.gov.in",
    },
    {
        "state_code": "KA",
        "label":      "Karnataka State Police",
        "url":        "https://ksp.gov.in",
        "domain":     "ksp.gov.in",
    },
    {
        "state_code": "TN",
        "label":      "Tamil Nadu Police",
        "url":        "https://tnpolice.gov.in",
        "domain":     "tnpolice.gov.in",
    },
    {
        "state_code": "TS",
        "label":      "Telangana State Police",
        "url":        "https://tspolice.gov.in",
        "domain":     "tspolice.gov.in",
    },
    {
        "state_code": "AP",
        "label":      "Andhra Pradesh Traffic Police",
        "url":        "https://aptrafficpolice.gov.in",
        "domain":     "aptrafficpolice.gov.in",
    },
    {
        "state_code": "UP",
        "label":      "Uttar Pradesh Police",
        "url":        "https://uppolice.gov.in",
        "domain":     "uppolice.gov.in",
    },
    {
        "state_code": "GJ",
        "label":      "Gujarat Police",
        "url":        "https://www.gujaratpolice.gov.in",
        "domain":     "gujaratpolice.gov.in",
    },
    {
        "state_code": "RJ",
        "label":      "Rajasthan Police",
        "url":        "https://police.rajasthan.gov.in",
        "domain":     "police.rajasthan.gov.in",
    },
    {
        "state_code": "KL",
        "label":      "Kerala Police",
        "url":        "https://keralapolice.gov.in",
        "domain":     "keralapolice.gov.in",
    },
    {
        "state_code": "WB",
        "label":      "West Bengal Police",
        "url":        "https://wbpolice.gov.in",
        "domain":     "wbpolice.gov.in",
    },
    {
        "state_code": "PB",
        "label":      "Punjab Police",
        "url":        "https://punjabpolice.gov.in",
        "domain":     "punjabpolice.gov.in",
    },
    {
        "state_code": "HR",
        "label":      "Haryana Police",
        "url":        "https://haryanapolice.gov.in",
        "domain":     "haryanapolice.gov.in",
    },
    {
        "state_code": "BR",
        "label":      "Bihar Police",
        "url":        "https://biharpolice.gov.in",
        "domain":     "biharpolice.gov.in",
    },
    {
        "state_code": "MP",
        "label":      "Madhya Pradesh Police",
        "url":        "https://mppolice.gov.in",
        "domain":     "mppolice.gov.in",
    },
    {
        "state_code": "OD",
        "label":      "Odisha Police",
        "url":        "https://odishapolice.gov.in",
        "domain":     "odishapolice.gov.in",
    },
    {
        "state_code": "AS",
        "label":      "Assam Police",
        "url":        "https://assampolice.gov.in",
        "domain":     "assampolice.gov.in",
    },
    {
        "state_code": "JH",
        "label":      "Jharkhand Police",
        "url":        "https://jhpolice.gov.in",
        "domain":     "jhpolice.gov.in",
    },
    {
        "state_code": "CT",
        "label":      "Chhattisgarh Police",
        "url":        "https://cgpolice.gov.in",
        "domain":     "cgpolice.gov.in",
    },
    {
        "state_code": "HP",
        "label":      "Himachal Pradesh Police",
        "url":        "https://hppolice.gov.in",
        "domain":     "hppolice.gov.in",
    },
    {
        "state_code": "UK",
        "label":      "Uttarakhand Police",
        "url":        "https://uttarakhandpolice.uk.gov.in",
        "domain":     "uttarakhandpolice.uk.gov.in",
    },
    {
        "state_code": "GA",
        "label":      "Goa Police",
        "url":        "https://goapolice.gov.in",
        "domain":     "goapolice.gov.in",
    },
    {
        "state_code": "JK",
        "label":      "J&K Police",
        "url":        "https://jkpolice.gov.in",
        "domain":     "jkpolice.gov.in",
    },
    {
        "state_code": "MN",
        "label":      "Manipur Police",
        "url":        "https://manipurpolice.gov.in",
        "domain":     "manipurpolice.gov.in",
    },
]

# Strict domain allowlist derived from the source list above
_ALLOWED_DOMAINS: set = {s["domain"].replace("www.", "") for s in GOV_SOURCES}

# Fine-related keywords used to extract relevant sections
_FINE_KEYWORDS = frozenset([
    "fine", "penalty", "challan", "offence", "offense", "violation",
    "section", "motor vehicle", "traffic rule", "₹", "rs.", "rupee",
    "imprisonment", "compoundable", "suspended", "helmet",
    "seatbelt", "drunk", "alcohol", "speed", "signal", "parking",
    "overloading", "insurance", "registration", "licence", "license",
])

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DriveLegalBot/1.0; "
        "Indian Traffic Law Updater; +https://parivahan.gov.in)"
    ),
    "Accept":          "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.5",
}


# ── Domain guard ──────────────────────────────────────────────────────────────

def _domain_allowed(url: str) -> bool:
    """Returns True only if the URL belongs to an explicitly listed .gov.in domain."""
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        return netloc in _ALLOWED_DOMAINS
    except Exception:
        return False


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_relevant_text(html: str, max_chars: int = 3500) -> str:
    """
    Extract fine/traffic-relevant text from raw HTML.
    Strategy: parse → strip noise → scan for relevant sections → truncate.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return html[:max_chars]

    # Remove noise elements
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "iframe", "noscript", "meta",
                     "link", "button", "select", "input"]):
        tag.decompose()

    raw = soup.get_text(separator="\n", strip=True)

    # Collapse whitespace
    raw = re.sub(r"[ \t]{2,}", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)

    lines = raw.splitlines()
    relevant_lines: List[str] = []
    in_relevant_section = False
    buffer: List[str] = []

    for line in lines:
        ll = line.lower()
        is_relevant = any(kw in ll for kw in _FINE_KEYWORDS)

        if is_relevant:
            in_relevant_section = True
            # Flush any buffered context lines
            relevant_lines.extend(buffer[-3:])
            buffer.clear()
            relevant_lines.append(line)
        elif in_relevant_section:
            # Keep up to 5 lines of context after a relevant line
            buffer.append(line)
            if len(buffer) > 5:
                in_relevant_section = False
                buffer.clear()

        if len("\n".join(relevant_lines)) >= max_chars:
            break

    result = "\n".join(relevant_lines).strip()
    return result[:max_chars] if result else raw[:max_chars]


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_page(source: Dict) -> Optional[Tuple[str, str, bool]]:
    """
    Fetch one source page.

    Returns:
      (url, extracted_text, changed)  — changed=False means hash matched (skip Groq)
      None                            — fetch failed (network / non-200 / blocked)
    """
    url = source["url"]

    # Hard domain guard — never fetch anything outside the allowlist
    if not _domain_allowed(url):
        log.warning("BLOCKED (not in allowlist): %s", url)
        return None

    try:
        resp = httpx.get(
            url,
            headers=_HEADERS,
            timeout=15.0,
            follow_redirects=True,
        )
    except httpx.TimeoutException:
        log.warning("Timeout: %s", url)
        return None
    except Exception as exc:
        log.warning("Fetch error %s — %s", url, exc)
        return None

    if resp.status_code != 200:
        log.warning("HTTP %d for %s", resp.status_code, url)
        return None

    html      = resp.text
    page_hash = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()[:32]
    old_hash  = get_page_hash(url)

    if old_hash == page_hash:
        log.debug("Unchanged: %s", source["label"])
        return (url, "", False)   # Not changed — skip Groq

    extracted = _extract_relevant_text(html)
    set_page_hash(url, page_hash)

    if not extracted.strip():
        log.debug("No relevant text found: %s", source["label"])
        return (url, "", False)

    log.info("Changed (%d chars): %s", len(extracted), source["label"])
    return (url, extracted, True)
