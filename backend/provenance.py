"""
Provenance enforcement for DriveLegal.

Every figure this service shows a member of the public must be traceable to a
legal instrument. This module is the single place that decides whether a value
is allowed out, so the rule cannot drift between call sites.

Background: an audit of the v3 graph found that of 1,799 legal and rule claims,
only 17% could be traced to a source and 104 contradicted the statute. The v5
serving graph withholds every field that failed verification; this module makes
the cascade honour that, and attaches the citation to whatever survives.

See docs/DATASET_VALIDATION_REPORT.md.
"""
from __future__ import annotations

from typing import Optional

__all__ = [
    "is_sourced", "fine_amount", "provenance_of", "citation_for",
    "REFUSAL_NO_VERIFIED_AMOUNT", "REFUSAL_SPEED_UNKNOWN",
]

REFUSAL_NO_VERIFIED_AMOUNT = (
    "I don't have a verified penalty amount for this in your state. "
    "The amount a state can compound an offence for is fixed by that state's own "
    "notification, and I could not source one. Check the official e-challan portal "
    "for what you actually owe."
)

REFUSAL_SPEED_UNKNOWN = (
    "I don't have a verified speed limit for this road and vehicle type. "
    "The limit on the posted sign governs — follow it."
)


def provenance_of(node: dict, field: str) -> dict:
    """Return the verification record for one field of a node, or {}."""
    if not isinstance(node, dict):
        return {}
    return ((node.get("_verification") or {}).get("fields") or {}).get(field) or {}


def is_sourced(node: dict, field: str) -> bool:
    """True only when the field passed verification AND carries a source URL.

    Both halves matter. A 'verified' status with no URL is not something we can
    show a citizen, because they cannot check it — and neither can we.
    """
    p = provenance_of(node, field)
    return p.get("status") == "verified" and bool(p.get("source_url"))


def fine_amount(fine_node: dict, slot: str = "first_offence") -> Optional[int]:
    """The amount this fine row is allowed to state, or None.

    Reads the normalised `fine_first` / `fine_repeat` shape rather than the raw
    column, and returns None unless the underlying field is sourced. Returning
    None makes the caller fall through the cascade or refuse — never guess.
    """
    if not isinstance(fine_node, dict):
        return None
    if not is_sourced(fine_node, slot):
        return None
    key = "fine_first" if slot == "first_offence" else "fine_repeat"
    money = fine_node.get(key) or {}
    amount = money.get("amount_inr")
    return amount if isinstance(amount, int) else None


def citation_for(fine_node: dict, violation_node: Optional[dict],
                 slot: str = "first_offence") -> dict:
    """The citation block that must accompany any amount we display."""
    p = provenance_of(fine_node, slot)
    key = "fine_first" if slot == "first_offence" else "fine_repeat"
    money = fine_node.get(key) or {}
    section = None
    if violation_node and is_sourced(violation_node, "mv_section"):
        section = violation_node.get("mv_section")
    return {
        "instrument": f"Motor Vehicles Act 1988, s.{section}" if section else None,
        "source_url": p.get("source_url"),
        "source_title": p.get("source_title"),
        "retrieved_at": p.get("retrieved_at"),
        "confidence": p.get("confidence"),
        "basis": money.get("basis", "flat"),
        "per_unit": money.get("per_unit"),
        "max_inr": money.get("max_inr"),
        "verified": True,
    }


def fallback_note(level: str, state_name: Optional[str]) -> Optional[str]:
    """Wording for an answer that came from a level above the one asked about.

    Section 200 of the Act lets a State Government specify compounding amounts by
    notification. Where it has not, the figure in the Act is the operative law —
    so this is a correct answer, not a degraded one. But the user must be told
    which level answered, or a central figure reads as a state-specific one.
    """
    if level != "central" or not state_name:
        return None
    return (f"{state_name} has no verified notification of a different amount, so the "
            f"figure fixed by the Motor Vehicles Act applies.")
