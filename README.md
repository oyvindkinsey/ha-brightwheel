# Brightwheel — Home Assistant Integration

Unofficial integration for the [Brightwheel](https://mybrightwheel.com/) parent app, intended for automating daycare check-in / check-out via geofence triggers.

> ⚠️ This is a reverse-engineered client built against the Android app (`co.kidcasa.app` v461). Brightwheel may change their API at any time and break this integration. Use at your own risk.

## What it does

For one configured child, it provides:

- **`binary_sensor.<child>_checked_in`** — polls every 120 s. `on` = currently checked in.
- **Service `brightwheel.checkin`** — submits a check-in for the configured child (signature + health screen + PIN).
- **Service `brightwheel.checkout`** — submits a check-out.

Add multiple children by creating multiple config entries.

## Installation (HACS)

1. HACS → "Custom repositories" → add this repo's URL, category "Integration".
2. Install "Brightwheel".
3. Restart Home Assistant.
4. Settings → Devices & Services → "Add Integration" → search "Brightwheel".

## Setup

You only need four things:

1. **Brightwheel email + password** for the parent/guardian account.
2. **2FA code** if your account has it enabled — Brightwheel will email it after you submit the password.
3. **The daycare QR payload.** Scan the daycare's check-in QR with any QR reader on your phone (e.g. the camera app) and copy the resulting JSON object — it contains `school_id` + `secret`.
4. **Your 4-digit check-in PIN.**

The integration handles everything else automatically:

- Logs in and stores the session token.
- Reads the QR to extract school + secret.
- Fetches your students from `/students_for_checkin/` and lets you pick which child + room this entry should manage.
- Auto-fetches the daycare's health screen questions before every check-in (answers them all as "true" — see caveat below).

## Example automation

```yaml
automation:
  - alias: "Brightwheel — auto check-in on arrival"
    trigger:
      - platform: zone
        entity_id: person.parent
        zone: zone.daycare
        event: enter
    condition:
      - condition: state
        entity_id: binary_sensor.ava_checked_in
        state: "off"
    action:
      - service: brightwheel.checkin

  - alias: "Brightwheel — auto check-out on departure"
    trigger:
      - platform: zone
        entity_id: person.parent
        zone: zone.daycare
        event: leave
    condition:
      - condition: state
        entity_id: binary_sensor.ava_checked_in
        state: "on"
    action:
      - service: brightwheel.checkout
```

## Caveats

- PerimeterX (`X-PX-*`) headers are not sent. Verified working as of 2026-05; if Brightwheel starts enforcing PX on the auth or checkin endpoints, this integration will break and need a token-refresh path.
- The signature uploaded is a bundled 1×1 transparent PNG. Brightwheel stores it without inspecting visual content.
- Health screen answers are always submitted as `true` (i.e. "no symptoms"). **If anyone in the household is actually sick, don't let the geofence automation fire — open the official app and answer honestly.**
- The session token from login is long-lived but not eternal. If it expires you'll see auth errors in the HA log; remove and re-add the integration entry to reauthenticate.
