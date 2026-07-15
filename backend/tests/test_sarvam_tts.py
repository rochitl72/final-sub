#!/usr/bin/env python3
"""Sarvam Bulbul v3 TTS — speaker compatibility."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_ENV = _BACKEND.parent / ".env"
if _ENV.exists():
    for _line in _ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))


class SarvamTTSUnit(unittest.TestCase):
    def test_v3_speakers_map_uses_only_valid_names(self):
        from sarvam_service import _BULBUL_V3_SPEAKERS

        for lang, speaker in _BULBUL_V3_SPEAKERS.items():
            self.assertTrue(lang.endswith("-IN"), lang)
            self.assertNotIn(
                speaker,
                {"meera", "pavithra", "anushka"},
                f"legacy v2 speaker {speaker!r} must not be used for {lang}",
            )

    def test_hindi_tts_live_when_key_set(self):
        if not os.environ.get("SARVAM_API_KEY"):
            self.skipTest("SARVAM_API_KEY not set")
        from sarvam_service import synthesize_speech

        audio = synthesize_speech("नमस्ते", "hi-IN")
        self.assertGreater(len(audio), 1000)


if __name__ == "__main__":
    unittest.main()
