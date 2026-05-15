"""DataUpdateCoordinator polling the Brightwheel check-in state."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BrightwheelClient, BrightwheelError
from .const import DOMAIN, POLL_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class BrightwheelCoordinator(DataUpdateCoordinator[bool | None]):
    def __init__(self, hass: HomeAssistant, client: BrightwheelClient, name: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {name}",
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
        )
        self.client = client

    async def _async_update_data(self) -> bool | None:
        try:
            return await self.client.get_checked_in()
        except BrightwheelError as e:
            raise UpdateFailed(str(e)) from e
