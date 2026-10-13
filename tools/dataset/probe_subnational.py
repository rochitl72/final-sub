#!/usr/bin/env python3
"""Probe official state/city traffic-fine sources for reachability, then cache
whatever is retrievable into the corpus."""
import json, subprocess, concurrent.futures as cf, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
C = ROOT / "data" / "audit" / "corpus"
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

# The 26 domains the repo's own gov_scraper.py is hardcoded to poll, plus the
# state transport departments and the city traffic police sites behind the
# enriched city nodes.
TARGETS = {
 "parivahan_fines": "https://parivahan.gov.in/parivahan//en/content/traffic-fines-challan",
 "morth_road_transport": "https://morth.nic.in/road-transport",
 "egazette_search": "https://egazette.nic.in/SearchEgazette.aspx?orgname=Motor+Vehicles",
 "echallan_parivahan": "https://echallan.parivahan.gov.in/",
 "DL_delhi_traffic": "https://delhitrafficpolice.nic.in/traffic-fines/",
 "MH_traffic": "https://mahatrafficpolice.gov.in",
 "KA_police": "https://ksp.gov.in",
 "TN_police": "https://tnpolice.gov.in",
 "TS_police": "https://tspolice.gov.in",
 "AP_traffic": "https://aptrafficpolice.gov.in",
 "UP_police": "https://uppolice.gov.in",
 "GJ_police": "https://www.gujaratpolice.gov.in",
 "RJ_police": "https://police.rajasthan.gov.in",
 "KL_police": "https://keralapolice.gov.in",
 "WB_police": "https://wbpolice.gov.in",
 "PB_police": "https://punjabpolice.gov.in",
 "HR_police": "https://haryanapolice.gov.in",
 "BR_police": "https://biharpolice.gov.in",
 "MP_police": "https://mppolice.gov.in",
 "OD_police": "https://odishapolice.gov.in",
 "AS_police": "https://assampolice.gov.in",
 "JH_police": "https://jhpolice.gov.in",
 "CG_police": "https://cgpolice.gov.in",
 "HP_police": "https://hppolice.gov.in",
 "UK_police": "https://uttarakhandpolice.uk.gov.in",
 "GA_police": "https://goapolice.gov.in",
 "JK_police": "https://jkpolice.gov.in",
 "MN_police": "https://manipurpolice.gov.in",
 # transport departments (where fine schedules are usually notified)
 "MH_transport": "https://transport.maharashtra.gov.in/",
 "KA_transport": "https://transport.karnataka.gov.in/",
 "TN_transport": "https://tnsta.tn.gov.in/",
 "DL_transport": "https://transport.delhi.gov.in/",
 "UP_transport": "https://uptransport.upsdc.gov.in/",
 "TG_transport": "https://transport.telangana.gov.in/",
 "KL_mvd": "https://mvd.kerala.gov.in/",
 "GJ_transport": "https://rtogujarat.gov.in/",
 # city traffic police
 "city_bengaluru": "https://btp.gov.in/",
 "city_hyderabad": "https://www.hyderabadpolice.gov.in/",
 "city_chennai": "https://www.chennaitrafficpolice.gov.in/",
 "city_mumbai": "https://trafficpolicemumbai.gov.in/",
 "city_pune": "https://punepolice.gov.in/",
 "city_kolkata": "https://kolkatatrafficpolice.gov.in/",
 "city_ahmedabad": "https://ahmedabadcitypolice.org/",
 "city_noida": "https://noidatraffic.in/",
}


def probe(item):
    key, url = item
    r = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", "35", "-A",
         "DriveLegalDataAudit/1.0 (dataset provenance verification)",
         "-o", str(C / f"sub_{key}.raw"), "-w", "%{http_code}|%{size_download}", url],
        capture_output=True, text=True)
    out = (r.stdout or "|").strip().split("|")
    code = out[0][-3:] if out[0] else "000"
    size = int(out[1]) if len(out) > 1 and out[1].isdigit() else 0
    ok = code == "200" and size > 2000
    if not ok:
        (C / f"sub_{key}.raw").unlink(missing_ok=True)
    return {"key": key, "url": url, "http": code, "bytes": size,
            "reachable": ok, "probed_at": NOW,
            "error": None if ok else (r.stderr or "").strip()[:120] or f"http {code}"}


with cf.ThreadPoolExecutor(max_workers=10) as ex:
    results = list(ex.map(probe, TARGETS.items()))

(ROOT / "data" / "audit" / "source_reachability.json").write_text(json.dumps(results, indent=1))
ok = [r for r in results if r["reachable"]]
print(f"reachable: {len(ok)}/{len(results)}\n")
for r in sorted(results, key=lambda x: (not x["reachable"], x["key"])):
    print(f"  {'OK  ' if r['reachable'] else 'DEAD'} {r['key']:<22} {r['http']:<4} "
          f"{r['bytes']:>8}  {r['url'][:60]}")
