from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .act import BarError, read_rear
from .const import CONF_HOST, CONF_TRACKER, DEFAULT_HOST, DEFAULT_TRACKER, DOMAIN


def _tracker_schema(values: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_TRACKER, default=values.get(CONF_TRACKER, DEFAULT_TRACKER)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
    }


class HeosSeatConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                await read_rear(user_input[CONF_HOST])
            except BarError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=f"HEOS Seat ({user_input[CONF_HOST]})", data=user_input)
        schema = {vol.Required(CONF_HOST, default=DEFAULT_HOST): str, **_tracker_schema(user_input or {})}
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HeosSeatOptionsFlow()


class HeosSeatOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        values = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=vol.Schema(_tracker_schema(values)))
