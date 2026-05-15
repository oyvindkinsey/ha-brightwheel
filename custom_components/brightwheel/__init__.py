"""Brightwheel integration entry point."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client

from .api import BrightwheelClient, BrightwheelError
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
    SERVICE_CHECKIN,
    SERVICE_CHECKOUT,
)
from .coordinator import BrightwheelCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR]

SIGNATURE_PNG = (Path(__file__).parent / "signature.png").read_bytes()

SERVICE_SCHEMA = vol.Schema({vol.Optional("config_entry_id"): str})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = aiohttp_client.async_get_clientsession(hass)
    data = entry.data

    client = BrightwheelClient(
        session,
        session_token=data[CONF_SESSION_TOKEN],
        client_uuid=data[CONF_CLIENT_UUID],
        guardian_id=data[CONF_GUARDIAN_ID],
        student_id=data[CONF_STUDENT_ID],
        room_id=data[CONF_ROOM_ID],
        school_id=data[CONF_SCHOOL_ID],
        qr_secret=data[CONF_QR_SECRET],
        pin=data[CONF_PIN],
        signature_png=SIGNATURE_PNG,
        time_zone=data.get(CONF_TIME_ZONE, DEFAULT_TIME_ZONE),
    )

    coordinator = BrightwheelCoordinator(hass, client, name=data[CONF_CHILD_NAME])
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_CHECKIN)
            hass.services.async_remove(DOMAIN, SERVICE_CHECKOUT)
    return unload_ok


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_CHECKIN):
        return

    def _resolve(call: ServiceCall) -> tuple[BrightwheelClient, BrightwheelCoordinator]:
        entries = hass.data.get(DOMAIN, {})
        entry_id = call.data.get("config_entry_id")
        if entry_id is None:
            if len(entries) != 1:
                raise HomeAssistantError(
                    "Multiple Brightwheel entries configured — pass config_entry_id"
                )
            entry_id = next(iter(entries))
        bag = entries.get(entry_id)
        if bag is None:
            raise HomeAssistantError(f"Unknown Brightwheel config_entry_id: {entry_id}")
        return bag["client"], bag["coordinator"]

    async def _do(call: ServiceCall, *, checked_in: bool) -> None:
        client, coordinator = _resolve(call)
        try:
            result = await client.transition(checked_in=checked_in)
        except BrightwheelError as e:
            raise HomeAssistantError(str(e)) from e
        if result is None:
            _LOGGER.info(
                "Brightwheel already %s — skipping",
                "checked in" if checked_in else "checked out",
            )
        await coordinator.async_request_refresh()

    async def _checkin(call: ServiceCall) -> None:
        await _do(call, checked_in=True)

    async def _checkout(call: ServiceCall) -> None:
        await _do(call, checked_in=False)

    hass.services.async_register(DOMAIN, SERVICE_CHECKIN, _checkin, schema=SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHECKOUT, _checkout, schema=SERVICE_SCHEMA)
