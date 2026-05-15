"""Switch that toggles the child's check-in state."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import BrightwheelError
from .const import CONF_CHILD_NAME, CONF_STUDENT_ID, DOMAIN
from .coordinator import BrightwheelCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bag = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BrightwheelCheckedInSwitch(
                bag["coordinator"],
                entry.entry_id,
                entry.data[CONF_CHILD_NAME],
                entry.data[CONF_STUDENT_ID],
            )
        ]
    )


class BrightwheelCheckedInSwitch(
    CoordinatorEntity[BrightwheelCoordinator], SwitchEntity
):
    _attr_has_entity_name = True
    _attr_name = "Checked in"
    _attr_icon = "mdi:account-school"

    def __init__(
        self,
        coordinator: BrightwheelCoordinator,
        entry_id: str,
        child_name: str,
        student_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_checked_in_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, student_id)},
            "name": child_name,
            "manufacturer": "Brightwheel",
        }

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._transition(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._transition(False)

    async def _transition(self, checked_in: bool) -> None:
        try:
            await self.coordinator.client.transition(checked_in=checked_in)
        except BrightwheelError as e:
            raise HomeAssistantError(str(e)) from e
        await self.coordinator.async_request_refresh()
