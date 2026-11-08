from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SeatConfigEntry
from .entity import SeatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SeatConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([SeatSwitch(entry.runtime_data, entry.entry_id, "centering")])


class SeatSwitch(SeatEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return self.controller.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_enabled(False)
