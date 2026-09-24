#!/usr/bin/env python3
"""Parse the consolidated MV Act and CMVR into addressable section/rule units."""
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
C = ROOT / "data" / "audit" / "corpus"

WORDNUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "twenty-five": 25, "fifty": 50, "hundred": 100, "thousand": 1000,
    "lakh": 100000,
}


def words_to_rupees(phrase: str):
    """'twenty-five thousand rupees' -> 25000. Returns list of amounts found."""
    out = []
    for m in re.finditer(
        r"((?:(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"fifteen|twenty|twenty-five|fifty)[\s-]+)+(?:hundred|thousand|lakh)"
        r"(?:[\s-]+(?:and[\s-]+)?(?:(?:one|two|three|four|five|six|seven|eight|nine|"
        r"ten|fifteen|twenty|fifty)[\s-]+)?(?:hundred|thousand)?)?)\s+rupees",
        phrase, re.I,
    ):
        toks = re.split(r"[\s-]+", m.group(1).lower())
        total, cur = 0, 0
        for tk in toks:
            if tk in ("and", ""):
                continue
            v = WORDNUM.get(tk)
            if v is None:
                continue
            if v >= 100:
                cur = (cur or 1) * v
                if v >= 1000:
                    total += cur
                    cur = 0
            else:
                cur += v
        total += cur
        if total:
            out.append(total)
    for m in re.finditer(r"(?:rupees|Rs\.?|₹)\s*([\d,]{3,})", phrase, re.I):
        try:
            out.append(int(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


# "183. Driving at excessive speed, etc. - (1) Whoever ..." appearing mid-line,
# optionally wrapped in [ ] to mark 2019-amendment insertions.
HEAD = re.compile(
    r"(?:(?<=^)|(?<=[.\]\s]))\[?\s*(\d{1,3}[A-Z]{0,3})\.\s+"          # number
    r"([A-Z“\"][^.\n]{3,150}?)"                                    # heading
    r"\s*(?:"
    r"\.?\s*[-–—]\s+"                                         # "Heading. - "
    r"|\.?\s*\[?\(1\)"                                                  # "Heading [(1) ..."
    r"|\.?\s*\[?\(\s*(?:1|i)\s*\)"
    r")"
)


def _split(text, label):
    marks = []
    for m in HEAD.finditer(text):
        head = m.group(2).strip()
        # reject prose false-positives like "...section 177 shall be taken"
        if head.lower().startswith(("and ", "or ", "the said", "shall", "which")):
            continue
        marks.append((m.start(), m.group(1), head))
    units = {}
    for i, (pos, num, head) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[pos:end]
        if num not in units or len(body) > len(units[num]["text"]):
            units[num] = {label: num, "heading": head, "text": body,
                          "amounts": sorted(set(words_to_rupees(body)))}
    return units


def parse_mva(text):
    return _split(text, "section")


def parse_cmvr(text):
    return _split(text, "rule")


if __name__ == "__main__":
    mva = parse_mva((C / "mva_1988_consolidated.txt").read_text())
    cmvr = parse_cmvr((C / "cmvr_1989.txt").read_text())
    (ROOT / "data" / "audit" / "mva_sections.json").write_text(json.dumps(mva, indent=1))
    (ROOT / "data" / "audit" / "cmvr_rules.json").write_text(json.dumps(cmvr, indent=1))
    print(f"MV Act sections parsed: {len(mva)}")
    print(f"CMVR rules parsed: {len(cmvr)}")
    for s in ["177", "183", "184", "185", "194D", "199A", "196", "129", "128"]:
        d = mva.get(s)
        print(f"  s.{s:<5} {'MISSING' if not d else d['heading'][:58] + '  amounts=' + str(d['amounts'][:6])}")
