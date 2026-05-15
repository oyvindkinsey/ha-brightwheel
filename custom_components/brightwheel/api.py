"""Async Brightwheel API client.

Reverse-engineered from co.kidcasa.app v461 by sniffing OkHttp with Frida.
PerimeterX (`X-PX-*`) headers are verified not required on the endpoints
this integration uses.

Important: the checkins endpoint accepts a minimal body shape (just object_ids)
and returns 201, but in that case it does NOT create a parent-feed entry —
the feed entry is rendered from the denormalized actor/target/room/health-
screen fields the real app sends. So this client fetches the rich data from
three GET endpoints before each transition and submits the full shape.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE = "https://schools.mybrightwheel.com/api/v1"
BASE_V2 = "https://schools.mybrightwheel.com/api/v2"


class BrightwheelError(Exception):
    """Raised on any non-success response from Brightwheel."""


class TwoFactorRequired(Exception):
    """Raised by ``start_session`` when the account requires a 2FA code."""

    def __init__(self, sent_to: list[str]) -> None:
        super().__init__(f"2FA code sent to: {', '.join(sent_to) or '<unknown>'}")
        self.sent_to = sent_to


_UNAUTH_HEADERS = {
    "X-Client-Name": "android",
    "X-Client-Version": "461",
    "X-Room-Mode": "false",
    "Accept": "application/json",
    "Accept-Language": "en-US",
    "User-Agent": "okhttp/4.12.0",
}


async def start_session(
    session: aiohttp.ClientSession,
    email: str,
    password: str,
    client_uuid: str,
) -> None:
    """POST /sessions/start to trigger 2FA. Raises TwoFactorRequired on success."""
    headers = {**_UNAUTH_HEADERS, "X-Client-UUID": client_uuid}
    body = {"user": {"email": email, "password": password}}
    async with session.post(
        f"{BASE}/sessions/start",
        json=body,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as r:
        if r.status != 200:
            raise BrightwheelError(
                f"sessions/start {r.status}: {(await r.text())[:300]}"
            )
        data = await r.json()
    if data.get("2fa_required"):
        raise TwoFactorRequired(data.get("2fa_code_sent_to") or [])


async def complete_session(
    session: aiohttp.ClientSession,
    email: str,
    password: str,
    client_uuid: str,
    two_fa_code: str | None = None,
) -> dict[str, Any]:
    """POST /sessions/. Returns ``{token, user{object_id, ...}, csrf}``."""
    headers = {**_UNAUTH_HEADERS, "X-Client-UUID": client_uuid}
    body: dict[str, Any] = {"user": {"email": email, "password": password}}
    if two_fa_code:
        body["2fa_code"] = two_fa_code
    async with session.post(
        f"{BASE}/sessions/",
        json=body,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as r:
        if r.status not in (200, 201):
            raise BrightwheelError(
                f"sessions/ {r.status}: {(await r.text())[:300]}"
            )
        return await r.json()


def parse_qr_data(qr_text: str) -> tuple[str, str]:
    """Parse a Brightwheel daycare QR JSON payload. Returns (school_id, qr_secret).

    Expects the decoded QR JSON object containing ``school_id`` and ``secret``.
    """
    import json

    try:
        j = json.loads(qr_text.strip())
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError("QR data is not valid JSON") from e

    if not isinstance(j, dict) or not j.get("school_id") or not j.get("secret"):
        raise ValueError("QR JSON did not contain school_id + secret")
    return j["school_id"], j["secret"]


class BrightwheelClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        session_token: str,
        client_uuid: str,
        guardian_id: str,
        student_id: str,
        room_id: str,
        school_id: str,
        qr_secret: str,
        pin: str,
        signature_png: bytes,
        time_zone: str,
    ) -> None:
        self._session = session
        self._session_token = session_token
        self._client_uuid = client_uuid
        self._guardian_id = guardian_id
        self._student_id = student_id
        self._room_id = room_id
        self._school_id = school_id
        self._qr_secret = qr_secret
        self._pin = pin
        self._signature_png = signature_png
        self._time_zone = time_zone

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-Parse-Session-Token": self._session_token,
            "X-Client-Name": "android",
            "X-Client-Version": "461",
            "X-Client-UUID": self._client_uuid,
            "X-Room-Mode": "false",
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "User-Agent": "okhttp/4.12.0",
        }

    async def _get_json(self, url: str, label: str) -> dict[str, Any]:
        async with self._session.get(
            url,
            headers=self._headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                raise BrightwheelError(
                    f"{label} {r.status}: {(await r.text())[:300]}"
                )
            return await r.json()

    async def _students_for_checkin(self) -> dict[str, Any]:
        url = (
            f"{BASE}/guardians/{self._guardian_id}/students_for_checkin/"
            f"?school_id={self._school_id}"
            f"&time_zone={quote(self._time_zone, safe='')}"
            f"&secret={self._qr_secret}"
        )
        return await self._get_json(url, "students_for_checkin")

    async def get_checked_in(self) -> bool | None:
        """Return the student's current checked_in state for the configured room.

        Returns None if the student/room pair isn't present in the response.
        """
        data = await self._students_for_checkin()
        for s in data.get("students", []):
            if s.get("student", {}).get("object_id") != self._student_id:
                continue
            for rs in s.get("room_states", []):
                if rs.get("room", {}).get("object_id") == self._room_id:
                    return bool(rs.get("checked_in"))
        return None

    async def list_students_and_rooms(self) -> list[dict[str, Any]]:
        """Flat list of (student, room) pairs visible to this guardian for this school."""
        data = await self._students_for_checkin()
        out: list[dict[str, Any]] = []
        for s in data.get("students", []):
            student = s.get("student") or {}
            sid = student.get("object_id")
            sname = f"{student.get('first_name','')} {student.get('last_name','')}".strip()
            for rs in s.get("room_states", []):
                room = rs.get("room") or {}
                out.append(
                    {
                        "student_id": sid,
                        "student_name": sname,
                        "room_id": room.get("object_id"),
                        "room_name": room.get("name", "room?"),
                    }
                )
        return out

    async def _student_and_room(self) -> tuple[dict[str, Any], dict[str, Any]]:
        data = await self._students_for_checkin()
        for s in data.get("students", []):
            student = s.get("student") or {}
            if student.get("object_id") != self._student_id:
                continue
            for rs in s.get("room_states", []):
                room = rs.get("room") or {}
                if room.get("object_id") == self._room_id:
                    return student, room
        raise BrightwheelError(
            f"student/room pair not found in students_for_checkin "
            f"(student_id={self._student_id}, room_id={self._room_id})"
        )

    async def _guardian_profile(self) -> dict[str, Any]:
        return await self._get_json(
            f"{BASE}/users/{self._guardian_id}/", "guardian_profile"
        )

    async def _health_screen_questions(self) -> list[dict[str, Any]]:
        data = await self._get_json(
            f"{BASE_V2}/schools/{self._school_id}/health_screen_questions/",
            "health_screen_questions",
        )
        return list(data.get("health_screen", {}).get("questions") or [])

    async def _upload_signature(self) -> str:
        j = await self._get_json(f"{BASE}/signature/presigned_url/", "presigned_url")
        fields = j["fields"]
        s3_url = j["url"]

        form = aiohttp.FormData()
        form.add_field("success_action_status", fields["success_action_status"])
        form.add_field("acl", fields["acl"])
        form.add_field("key", fields["key"])
        form.add_field("Content-Type", fields["content_type"])
        form.add_field("policy", fields["policy"])
        form.add_field("x-amz-credential", fields["x_amz_credential"])
        form.add_field("x-amz-algorithm", fields["x_amz_algorithm"])
        form.add_field("x-amz-date", fields["x_amz_date"])
        form.add_field("x-amz-signature", fields["x_amz_signature"])
        form.add_field(
            "file",
            self._signature_png,
            filename="signature.png",
            content_type="image/png",
        )

        async with self._session.post(
            s3_url,
            data=form,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status not in (200, 201, 204):
                raise BrightwheelError(
                    f"s3 signature upload {r.status}: {(await r.text())[:300]}"
                )

        return f"{s3_url}/{fields['key']}"

    async def transition(self, *, checked_in: bool) -> dict[str, Any]:
        """Perform a check-in (True) or check-out (False).

        Fetches the rich denormalized data needed for the parent-feed entry
        to render, then POSTs the full body.
        """
        guardian = await self._guardian_profile()
        student, room = await self._student_and_room()
        questions_template = await self._health_screen_questions()
        sig_url = await self._upload_signature()

        questions = []
        for q in questions_template:
            entry = {
                "object_id": q.get("object_id"),
                "answer_type": q.get("answer_type", "boolean"),
                "default_answer": q.get("default_answer", "false"),
                "expected_answer": q.get("expected_answer", "true"),
                "question": q.get("question", ""),
            }
            if checked_in:
                entry["answer"] = "true"
            questions.append(entry)

        actor = {
            "user_type": "guardian",
            "type": "guardian",
            "object_id": self._guardian_id,
            "raw_passcode": self._pin,
            "first_name": guardian.get("first_name"),
            "last_name": guardian.get("last_name"),
            "email": guardian.get("email"),
            "auth_phone_number": guardian.get("auth_phone_number"),
            "profile_photo": guardian.get("profile_photo"),
            "authentication_methods": guardian.get("authentication_methods") or [],
            "school_invites": guardian.get("school_invites") or [],
            "intercom_user_jwt": guardian.get("intercom_user_jwt"),
            "activated": guardian.get("activated", True),
            "is_admin": guardian.get("is_admin", False),
        }

        target = {
            **student,
            "type": "student",
        }

        body = {
            "checkin_code": self._pin,
            "school_id": self._school_id,
            "secret": self._qr_secret,
            "checkins": [
                {
                    "checked_in": checked_in,
                    "actor": actor,
                    "target": target,
                    "room": room,
                    "signature": {"url": sig_url},
                    "health_screen": {"questions": questions},
                }
            ],
        }

        headers = {**self._headers, "Content-Type": "application/json; charset=UTF-8"}
        async with self._session.post(
            f"{BASE}/checkins/",
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 201:
                raise BrightwheelError(
                    f"checkins {r.status}: {(await r.text())[:300]}"
                )
            return await r.json()
