from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SeatConfigEntry
from .entity import SeatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SeatConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        OffsetSensor(entry.runtime_data, entry.entry_id, key, index)
        for index, key in enumerate(("left_offset", "right_offset"))
    )


class OffsetSensor(SeatEntity, SensorEntity):
    """Décalage appliqué à un arrière, en dB, par rapport à son niveau d'origine."""

    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, controller, entry_id: str, key: str, index: int) -> None:
        super().__init__(controller, entry_id, key)
        self._index = index

    @property
    def native_value(self) -> int:
        return self.controller.seat.applied[self._index]
