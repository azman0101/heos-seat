"""HEOS Seat : centre le son des arrières de la barre HEOS sur la place occupée du canapé."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_HOST, CONF_TRACKER, DEFAULT_TRACKER, DOMAIN
from .controller import SeatController

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SeatConfigEntry = ConfigEntry[SeatController]


def _controller(hass: HomeAssistant) -> SeatController:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("HEOS Seat n'est pas configuré")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services utilisés par l'onglet « Place » de l'app HEOS Fader (dépôt heos-uniffi)."""

    def handler(method, *fields):
        async def handle(call: ServiceCall) -> ServiceResponse:
            return await getattr(_controller(hass), method)(*(call.data.get(f) for f in fields))

        return handle

    name = {vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=40))}
    db = vol.All(vol.Coerce(int), vol.Range(min=-12, max=12))
    services = {
        "save_spot": (
            handler("async_save_spot", "name", "left_db", "right_db"),
            {**name, vol.Optional("left_db"): db, vol.Optional("right_db"): db},
        ),
        "set_spot_levels": (
            handler("async_set_spot_levels", "name", "left_db", "right_db"),
            {**name, vol.Required("left_db"): db, vol.Required("right_db"): db},
        ),
        "remove_spot": (handler("async_remove_spot", "name"), name),
        "clear_calibration": (handler("async_clear_calibration"), {}),
    }
    for service, (handle, schema) in services.items():
        hass.services.async_register(
            DOMAIN, service, handle, vol.Schema(schema), supports_response=SupportsResponse.OPTIONAL
        )

    async def set_enabled(call: ServiceCall) -> None:
        await _controller(hass).async_set_enabled(call.data["enabled"])

    async def status(call: ServiceCall) -> ServiceResponse:
        return _controller(hass).status()

    hass.services.async_register(
        DOMAIN, "set_enabled", set_enabled, vol.Schema({vol.Required("enabled"): cv.boolean})
    )
    hass.services.async_register(DOMAIN, "status", status, supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SeatConfigEntry) -> bool:
    options = {**entry.data, **entry.options}
    controller = SeatController(
        hass,
        entry.entry_id,
        host=options[CONF_HOST],
        tracker=options.get(CONF_TRACKER, DEFAULT_TRACKER),
    )
    await controller.async_start()
    entry.runtime_data = controller
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SeatConfigEntry) -> bool:
    if ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return ok


async def _reload(hass: HomeAssistant, entry: SeatConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
