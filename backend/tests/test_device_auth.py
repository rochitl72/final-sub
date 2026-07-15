#!/usr/bin/env python3
"""
test_device_auth.py — Device-ID authentication + session scoping
================================================================
Run: python3 backend/tests/test_device_auth.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from api import app  # noqa: E402
from auth import decode_jwt, upsert_device_user  # noqa: E402

VALID_DEVICE = "a1b2c3d4-e5f6-4789-a012-3456789abcde"
OTHER_DEVICE = "b2c3d4e5-f6a7-4890-b123-456789abcdef"


class DeviceAuthUnit(unittest.TestCase):
    def test_upsert_idempotent(self):
        u1 = upsert_device_user(VALID_DEVICE)
        u2 = upsert_device_user(VALID_DEVICE)
        self.assertEqual(u1["id"], u2["id"])
        self.assertTrue(u2["name"].startswith("Guest "))

    def test_different_devices_different_users(self):
        u1 = upsert_device_user(VALID_DEVICE)
        u2 = upsert_device_user(OTHER_DEVICE)
        self.assertNotEqual(u1["id"], u2["id"])


class DeviceAuthAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_register_device_returns_jwt(self):
        r = self.client.post("/auth/device", json={"device_id": VALID_DEVICE})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("access_token", body)
        self.assertEqual(body["token_type"], "bearer")
        self.assertGreater(body["expires_in"], 0)
        self.assertEqual(body["user"]["email"], f"guest-{VALID_DEVICE}@device.local")
        self.assertTrue(decode_jwt(body["access_token"]))

    def test_invalid_device_id_rejected(self):
        r = self.client.post("/auth/device", json={"device_id": "not-a-uuid"})
        self.assertIn(r.status_code, (400, 422), r.text)
        r2 = self.client.post(
            "/auth/device",
            json={"device_id": "00000000-0000-4000-8000-000000000000"},
        )
        self.assertEqual(r2.status_code, 200)

    def test_me_requires_auth(self):
        r = self.client.get("/auth/me")
        self.assertEqual(r.status_code, 401)

    def test_me_with_bearer(self):
        reg = self.client.post("/auth/device", json={"device_id": VALID_DEVICE}).json()
        token = reg["access_token"]
        r = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], reg["user"]["id"])

    def test_google_routes_removed(self):
        for path in ("/auth/google", "/auth/google/mobile", "/auth/google/callback"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 404, path)

    def test_logout_ok(self):
        r = self.client.post("/auth/logout")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))

    def test_expired_jwt_rejected(self):
        reg = self.client.post("/auth/device", json={"device_id": VALID_DEVICE}).json()
        import os
        from jose import jwt as jose_jwt

        bad = jose_jwt.encode(
            {"sub": reg["user"]["id"], "email": "x@y.z", "name": "X", "exp": 1, "iat": 1},
            os.environ.get("SESSION_SECRET", "drivelegal-dev-secret-change-me-in-production"),
            algorithm="HS256",
        )
        r = self.client.get("/auth/me", headers={"Authorization": f"Bearer {bad}"})
        self.assertEqual(r.status_code, 401)


class SessionScoping(unittest.TestCase):
    """Authenticated sessions are isolated per device user."""

    def setUp(self):
        self.client = TestClient(app)

    def _token(self, device_id: str) -> str:
        r = self.client.post("/auth/device", json={"device_id": device_id})
        r.raise_for_status()
        return r.json()["access_token"]

    def test_sessions_scoped_to_user(self):
        t_a = self._token(VALID_DEVICE)
        t_b = self._token(OTHER_DEVICE)
        h_a = {"Authorization": f"Bearer {t_a}"}
        h_b = {"Authorization": f"Bearer {t_b}"}

        s_a = self.client.post(
            "/api/sessions/new", json={"mode": "static"}, headers=h_a
        ).json()["session_id"]
        s_b = self.client.post(
            "/api/sessions/new", json={"mode": "static"}, headers=h_b
        ).json()["session_id"]

        list_a = self.client.get("/api/sessions", headers=h_a).json()["sessions"]
        list_b = self.client.get("/api/sessions", headers=h_b).json()["sessions"]

        ids_a = {x["id"] for x in list_a}
        ids_b = {x["id"] for x in list_b}

        self.assertIn(s_a, ids_a)
        self.assertNotIn(s_b, ids_a)
        self.assertIn(s_b, ids_b)
        self.assertNotIn(s_a, ids_b)

    def test_anonymous_list_excludes_other_users_sessions(self):
        """Without Bearer, list must not leak device-scoped sessions."""
        t_a = self._token(VALID_DEVICE)
        user_sid = self.client.post(
            "/api/sessions/new", json={"mode": "static"},
            headers={"Authorization": f"Bearer {t_a}"},
        ).json()["session_id"]
        anon_sid = self.client.post(
            "/api/sessions/new", json={"mode": "static"},
        ).json()["session_id"]

        listed = self.client.get("/api/sessions").json()["sessions"]
        ids = {x["id"] for x in listed}
        self.assertIn(anon_sid, ids)
        self.assertNotIn(user_sid, ids)


class AuthWithDialogFlow(unittest.TestCase):
    """Smoke: authed user can complete a minimal calculator turn."""

    def setUp(self):
        self.client = TestClient(app)
        import dialog_manager
        from dynamic_chatbot import extract_and_reply as _extract
        from llm_chatbot import handle_freeform

        dialog_manager.get_dialog_manager(
            freeform_fn=lambda s, t: handle_freeform(s, t),
            dynamic_fn=lambda s, t: _extract(s, t),
        )

    def test_authed_static_slot_flow(self):
        reg = self.client.post(
            "/auth/device", json={"device_id": VALID_DEVICE}
        ).json()
        headers = {"Authorization": f"Bearer {reg['access_token']}"}

        data = self.client.post(
            "/api/sessions/new", json={"mode": "static"}, headers=headers
        ).json()
        sid = data["session_id"]

        self.client.put(
            f"/api/session/{sid}/geo_mode", json={"mode": "chat"}, headers=headers
        ).raise_for_status()
        for slot, value in (
            ("state_code", "KA"),
            ("city_code", "BLR"),
            ("road_bucket", "street"),
            ("vehicle_segment", "two_wheeler"),
            ("violation_category", "safety"),
        ):
            self.client.post(
                f"/api/session/{sid}/slot",
                json={"slot": slot, "value": value},
                headers=headers,
            ).raise_for_status()

        turn = self.client.post(
            f"/api/session/{sid}/turn",
            json={"message": "no helmet"},
            headers=headers,
        ).json()
        payload = turn.get("payload") or turn
        intent = payload.get("intent")
        self.assertIn(intent, ("answer", "ask_slot", "narrate"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
