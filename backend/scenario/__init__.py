"""
Scenario Engine — multi-person, multi-offence understanding for DriveLegal.

    text ──► extract (LLM in AI mode / rules offline) ──► Scenario graph
         ──► reasoner (law layer from data/drivelegal.db) ──► per-person findings
         ──► planner (ask only what changes the answer) ──► composer (grounded reply)

The LLM only reads the story into structured facts (each backed by a quote);
which offences apply, to whom, and for how much is decided deterministically.
"""
from .engine import maybe_handle, analyse_text  # noqa: F401
