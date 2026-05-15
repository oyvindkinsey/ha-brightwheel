"""Binary sensor exposing the child's check-in state."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CHILD_NAME, CONF_STUDENT_ID, DOMAIN
from .coordinator import BrightwheelCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BrightwheelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            BrightwheelCheckedInSensor(
                coordinator,
                entry.entry_id,
                entry.data[CONF_CHILD_NAME],
                entry.data[CONF_STUDENT_ID],
            )
        ]
    )


class BrightwheelCheckedInSensor(
    CoordinatorEntity[BrightwheelCoordinator], BinarySensorEntity
):
    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_has_entity_name = True
    _attr_name = "Checked in"

    def __init__(
        self,
        coordinator: BrightwheelCoordinator,
        entry_id: str,
        child_name: str,
        student_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_checked_in"
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
