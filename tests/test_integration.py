"""Intégration dans un vrai Home Assistant de test, barre simulée (aucun accès réseau)."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.heos_seat.act import BarError
from custom_components.heos_seat.const import DOMAIN

TRACKER = "sensor.fp400_tracked_people"
LEFT, RIGHT, PASSER = (-45, 189), (-5, 84), (-150, 300)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def targets(*points):
    return {"targets": [{"id": i, "x": x, "y": y} for i, (x, y) in enumerate(points)]}


@pytest.fixture
def bar():
    """Barre simulée : arrières à 0 dB (brut 12) ; la liste garde les écritures."""
    writes = []

    async def write(host, side, raw):
        writes.append((side, raw))

    with (
        patch("custom_components.heos_seat.controller.read_rear", AsyncMock(return_value=(12, 12))),
        patch("custom_components.heos_seat.config_flow.read_rear", AsyncMock(return_value=(12, 12))),
        patch("custom_components.heos_seat.controller.write_rear", side_effect=write),
    ):
        yield writes


async def setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.168.1.12", "tracker": TRACKER})
    entry.add_to_hass(hass)
    hass.states.async_set(TRACKER, "0", targets())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def call(hass, service, **data):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, return_response=True)


async def save(hass, name, where, **levels):
    hass.states.async_set(TRACKER, "1", targets(where))
    return await call(hass, "save_spot", name=name, **levels)


async def test_config_flow(hass: HomeAssistant, bar) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.168.1.12", "tracker": TRACKER}
    )
    assert result["type"] == "create_entry"


async def test_config_flow_unreachable_bar(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch("custom_components.heos_seat.config_flow.read_rear", AsyncMock(side_effect=BarError("x"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "10.0.0.1", "tracker": TRACKER}
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_saving_needs_exactly_one_person(hass: HomeAssistant, bar) -> None:
    await setup(hass)
    hass.states.async_set(TRACKER, "2", targets(LEFT, RIGHT))
    with pytest.raises(ServiceValidationError):
        await call(hass, "save_spot", name="left")


async def test_spots_presets_and_edits(hass: HomeAssistant, bar) -> None:
    await setup(hass)
    status = await save(hass, "left", LEFT)
    assert status["spots"] == [{"name": "left", "x": -45.0, "y": 189.0, "left_db": -5, "right_db": 7}]
    status = await save(hass, "fauteuil", (80, 150))
    assert status["spots"][1]["left_db"] == 0  # position libre : réglage à choisir plus tard
    status = await call(hass, "set_spot_levels", name="fauteuil", left_db=3, right_db=-2)
    assert status["spots"][1]["right_db"] == -2
    status = await save(hass, "fauteuil", (82, 151))  # relevé à nouveau : le réglage est gardé
    assert status["spots"][1]["left_db"] == 3 and status["spots"][1]["x"] == 82.0
    status = await call(hass, "remove_spot", name="fauteuil")
    assert [s["name"] for s in status["spots"]] == ["left"]
    with pytest.raises(ServiceValidationError):
        await call(hass, "remove_spot", name="fauteuil")
    assert not bar  # rien n'est écrit tant que le centrage est arrêté


async def test_follow_left_seat_and_restore(hass: HomeAssistant, bar, freezer) -> None:
    await setup(hass)
    await save(hass, "left", LEFT)
    await save(hass, "right", RIGHT)
    await hass.services.async_call(DOMAIN, "set_enabled", {"enabled": True}, blocking=True)
    switch = hass.states.async_all("switch")[0]
    assert switch.state == "on"

    # Assis à gauche pendant que quelqu'un passe devant la TV.
    for i in range(6):
        freezer.tick(0.5)
        hass.states.async_set(TRACKER, "2", {**targets(LEFT, PASSER), "n": i})
        await hass.async_block_till_done()
    assert bar == [("Left", 7), ("Right", 19)]  # G −5 dB, D +7 dB

    status = await call(hass, "status")
    assert status["offsets_db"] == {"left": -5, "right": 7}
    assert status["levels_db"] == {"left": -5, "right": 7}
    assert [t["followed"] for t in status["targets"]] == [True, False]
    assert sorted(s.state for s in hass.states.async_all("sensor") if "heos" in s.entity_id) == ["-5", "7"]

    bar.clear()
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch.entity_id}, blocking=True)
    assert bar == [("Left", 12), ("Right", 12)]


async def test_unload_restores_and_resumes(hass: HomeAssistant, bar, freezer) -> None:
    entry = await setup(hass)
    await save(hass, "left", LEFT)
    await hass.services.async_call(DOMAIN, "set_enabled", {"enabled": True}, blocking=True)
    for i in range(6):
        freezer.tick(0.5)
        hass.states.async_set(TRACKER, "1", {**targets(LEFT), "n": i})
        await hass.async_block_till_done()
    bar.clear()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert bar == [("Left", 12), ("Right", 12)]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.enabled
    assert [s.name for s in entry.runtime_data.calibration.spots.values()] == ["left"]
