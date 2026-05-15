"""Config flow for Brightwheel.

Steps:
  1. user           — email + password         → POST /sessions/start (triggers 2FA)
  2. two_factor     — 2FA code                 → POST /sessions/      (gets token + guardian_id)
  3. qr             — paste decoded QR JSON    → derives school_id + secret
  4. student        — pick student/room        → from students_for_checkin
  5. pin            — daycare PIN              → finalize entry
"""
from __future__ import annotations

import logging
import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import selector

from .api import (
    BrightwheelClient,
    BrightwheelError,
    TwoFactorRequired,
    complete_session,
    parse_qr_data,
    start_session,
)
from .const import (
    CONF_CHILD_NAME,
    CONF_CLIENT_UUID,
    CONF_GUARDIAN_ID,
    CONF_PIN,
    CONF_QR_SECRET,
    CONF_ROOM_ID,
    CONF_SCHOOL_ID,
    CONF_SESSION_TOKEN,
    CONF_STUDENT_ID,
    CONF_TIME_ZONE,
    DEFAULT_TIME_ZONE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class BrightwheelConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._client_uuid: str = str(uuid.uuid4())
        self._email: str | None = None
        self._password: str | None = None
        self._session_token: str | None = None
        self._guardian_id: str | None = None
        self._school_id: str | None = None
        self._qr_secret: str | None = None
        self._time_zone: str = DEFAULT_TIME_ZONE
        # student_id -> ({"student_id":..,"room_id":..,"label":..})
        self._student_options: dict[str, dict[str, str]] = {}
        self._student_id: str | None = None
        self._room_id: str | None = None
        self._child_name: str | None = None

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input["email"]
            self._password = user_input["password"]
            session = aiohttp_client.async_get_clientsession(self.hass)
            try:
                await start_session(session, self._email, self._password, self._client_uuid)
            except TwoFactorRequired:
                return await self.async_step_two_factor()
            except BrightwheelError:
                _LOGGER.exception("sessions/start failed")
                errors["base"] = "auth"
            else:
                # No 2FA needed; complete immediately.
                return await self._complete_login(errors)

        schema = vol.Schema(
            {
                vol.Required("email"): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
                ),
                vol.Required("password"): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_two_factor(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            return await self._complete_login(errors, two_fa_code=user_input["two_fa_code"])

        schema = vol.Schema({vol.Required("two_fa_code"): str})
        return self.async_show_form(
            step_id="two_factor", data_schema=schema, errors=errors
        )

    async def _complete_login(
        self, errors: dict[str, str], two_fa_code: str | None = None
    ) -> ConfigFlowResult:
        assert self._email and self._password
        session = aiohttp_client.async_get_clientsession(self.hass)
        try:
            data = await complete_session(
                session, self._email, self._password, self._client_uuid, two_fa_code
            )
        except BrightwheelError:
            _LOGGER.exception("sessions/ failed")
            errors["base"] = "auth"
            schema = vol.Schema({vol.Required("two_fa_code"): str}) if two_fa_code else \
                vol.Schema({vol.Required("email"): str, vol.Required("password"): str})
            step = "two_factor" if two_fa_code else "user"
            return self.async_show_form(step_id=step, data_schema=schema, errors=errors)

        self._session_token = data["token"]
        self._guardian_id = data["user"]["object_id"]
        return await self.async_step_qr()

    async def async_step_qr(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                school_id, secret = parse_qr_data(user_input["qr_data"])
            except ValueError:
                errors["qr_data"] = "bad_qr"
            else:
                self._school_id = school_id
                self._qr_secret = secret
                self._time_zone = user_input.get(CONF_TIME_ZONE, DEFAULT_TIME_ZONE)
                return await self._fetch_students(errors)

        schema = vol.Schema(
            {
                vol.Required("qr_data"): str,
                vol.Optional(CONF_TIME_ZONE, default=DEFAULT_TIME_ZONE): str,
            }
        )
        return self.async_show_form(step_id="qr", data_schema=schema, errors=errors)

    async def _fetch_students(self, errors: dict[str, str]) -> ConfigFlowResult:
        assert self._session_token and self._guardian_id and self._school_id and self._qr_secret
        session = aiohttp_client.async_get_clientsession(self.hass)
        # Reuse the runtime client to call students_for_checkin
        client = BrightwheelClient(
            session,
            session_token=self._session_token,
            client_uuid=self._client_uuid,
            guardian_id=self._guardian_id,
            student_id="",
            room_id="",
            school_id=self._school_id,
            qr_secret=self._qr_secret,
            pin="",
            signature_png=b"",
            time_zone=self._time_zone,
        )
        try:
            pairs = await client.list_students_and_rooms()
        except BrightwheelError:
            _LOGGER.exception("students_for_checkin failed")
            errors["base"] = "students_fetch"
            return self.async_show_form(
                step_id="qr",
                data_schema=vol.Schema({vol.Required("qr_data"): str}),
                errors=errors,
            )

        self._student_options = {}
        for p in pairs:
            sid = p["student_id"]
            rid = p["room_id"]
            if not sid or not rid:
                continue
            key = f"{sid}|{rid}"
            label = f"{p['student_name']} — {p['room_name']}"
            self._student_options[key] = {
                "student_id": sid,
                "room_id": rid,
                "label": label,
                "name": p["student_name"],
            }

        if not self._student_options:
            errors["base"] = "no_students"
            return self.async_show_form(
                step_id="qr",
                data_schema=vol.Schema({vol.Required("qr_data"): str}),
                errors=errors,
            )

        return await self.async_step_student()

    async def async_step_student(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = [
            selector.SelectOptionDict(value=k, label=v["label"])
            for k, v in self._student_options.items()
        ]
        if user_input is not None:
            chosen = self._student_options.get(user_input["student"])
            if not chosen:
                errors["student"] = "invalid"
            else:
                self._student_id = chosen["student_id"]
                self._room_id = chosen["room_id"]
                self._child_name = user_input.get(CONF_CHILD_NAME) or chosen["name"]
                await self.async_set_unique_id(
                    f"{self._guardian_id}:{self._student_id}"
                )
                self._abort_if_unique_id_configured()
                return await self.async_step_pin()

        schema = vol.Schema(
            {
                vol.Required("student"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(CONF_CHILD_NAME): str,
            }
        )
        return self.async_show_form(
            step_id="student", data_schema=schema, errors=errors
        )

    async def async_step_pin(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = user_input[CONF_PIN]
            # No live validation of PIN — Brightwheel only checks it on the
            # /checkins/ POST. We trust the user to type it correctly.
            return self.async_create_entry(
                title=self._child_name or "Brightwheel",
                data={
                    CONF_SESSION_TOKEN: self._session_token,
                    CONF_GUARDIAN_ID: self._guardian_id,
                    CONF_STUDENT_ID: self._student_id,
                    CONF_ROOM_ID: self._room_id,
                    CONF_SCHOOL_ID: self._school_id,
                    CONF_QR_SECRET: self._qr_secret,
                    CONF_PIN: pin,
                    CONF_CHILD_NAME: self._child_name,
                    CONF_CLIENT_UUID: self._client_uuid,
                    CONF_TIME_ZONE: self._time_zone,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_PIN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                )
            }
        )
        return self.async_show_form(step_id="pin", data_schema=schema, errors=errors)
