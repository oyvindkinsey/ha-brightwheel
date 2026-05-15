#!/usr/bin/env python3
"""Interactive Brightwheel CLI — exercises the full login → checkin lifecycle.

Imports the async client from the HA integration so wire behavior stays in sync.
"""
from __future__ import annotations

import asyncio
import getpass
import sys
import uuid
from pathlib import Path

import aiohttp

HERE = Path(__file__).parent
# Import api.py directly, bypassing the package __init__ which pulls in homeassistant.
sys.path.insert(0, str(HERE / "custom_components" / "brightwheel"))

from api import (  # noqa: E402
    BrightwheelClient,
    BrightwheelError,
    TwoFactorRequired,
    complete_session,
    parse_qr_data,
    start_session,
)

SIGNATURE_PATH = HERE / "signature.png"
DEFAULT_TIME_ZONE = "America/Los_Angeles"


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or (default or "")


async def _login(session: aiohttp.ClientSession, client_uuid: str) -> dict:
    while True:
        email = _prompt("Email")
        password = getpass.getpass("Password: ")
        try:
            await start_session(session, email, password, client_uuid)
        except TwoFactorRequired as tfr:
            print(f"2FA code sent to: {', '.join(tfr.sent_to) or '?'}")
            code = _prompt("2FA code")
            try:
                return await complete_session(session, email, password, client_uuid, code)
            except BrightwheelError as e:
                print(f"login failed: {e}\n")
                continue
        except BrightwheelError as e:
            print(f"start_session failed: {e}\n")
            continue
        # No 2FA path
        try:
            return await complete_session(session, email, password, client_uuid)
        except BrightwheelError as e:
            print(f"login failed: {e}\n")
            continue


async def _pick_student(client: BrightwheelClient) -> tuple[str, str, str]:
    pairs = await client.list_students_and_rooms()
    if not pairs:
        raise SystemExit("No students returned for this QR.")
    for i, p in enumerate(pairs, 1):
        print(f"  {i}. {p['student_name']} — {p['room_name']}")
    while True:
        try:
            idx = int(_prompt("Pick number")) - 1
            if 0 <= idx < len(pairs):
                p = pairs[idx]
                return p["student_id"], p["room_id"], p["student_name"]
        except ValueError:
            pass
        print("invalid selection")


async def _state_loop(client: BrightwheelClient, name: str) -> None:
    while True:
        try:
            state = await client.get_checked_in()
        except BrightwheelError as e:
            print(f"state fetch error: {e}")
            state = None
        label = "checked in" if state else "checked out" if state is False else "unknown"
        print(f"\n{name}: {label}")
        choice = _prompt("[i]checkin / [o]checkout / [r]refresh / [q]uit").lower()
        if choice == "q":
            return
        if choice == "r":
            continue
        if choice in ("i", "o"):
            checked_in = choice == "i"
            try:
                result = await client.transition(checked_in=checked_in)
                if result is None:
                    print(f"already {'checked in' if checked_in else 'checked out'} — no action")
                else:
                    print(f"{'check-in' if checked_in else 'check-out'} submitted")
            except BrightwheelError as e:
                print(f"transition failed: {e}")
        else:
            print("?")


async def main() -> None:
    client_uuid = str(uuid.uuid4())
    signature_png = SIGNATURE_PATH.read_bytes()

    async with aiohttp.ClientSession() as session:
        print("=== Login ===")
        data = await _login(session, client_uuid)
        token = data["token"]
        guardian_id = data["user"]["object_id"]
        print(f"token: {token[:8]}…  guardian_id: {guardian_id}")

        print("\n=== QR ===")
        qr_text = _prompt("Decoded QR JSON")
        school_id, qr_secret = parse_qr_data(qr_text)
        print(f"school_id: {school_id}")
        time_zone = _prompt("Time zone", DEFAULT_TIME_ZONE)

        stub = BrightwheelClient(
            session,
            session_token=token,
            client_uuid=client_uuid,
            guardian_id=guardian_id,
            student_id="",
            room_id="",
            school_id=school_id,
            qr_secret=qr_secret,
            pin="",
            signature_png=signature_png,
            time_zone=time_zone,
        )

        print("\n=== Student ===")
        student_id, room_id, student_name = await _pick_student(stub)

        print("\n=== PIN ===")
        pin = getpass.getpass("PIN: ")

        client = BrightwheelClient(
            session,
            session_token=token,
            client_uuid=client_uuid,
            guardian_id=guardian_id,
            student_id=student_id,
            room_id=room_id,
            school_id=school_id,
            qr_secret=qr_secret,
            pin=pin,
            signature_png=signature_png,
            time_zone=time_zone,
        )

        await _state_loop(client, student_name)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
