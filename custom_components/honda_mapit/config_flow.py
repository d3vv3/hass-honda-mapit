"""Config flow for Honda Mapit."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MapitApiClient, MapitAuthError, MapitConnectionError
from .const import CONF_ACCOUNT_ID, DOMAIN


async def validate_input(hass, data: dict[str, Any]) -> dict[str, str]:
    """Validate the user input allows us to connect."""
    client = MapitApiClient(
        async_get_clientsession(hass),
        data[CONF_EMAIL],
        data[CONF_PASSWORD],
    )
    account = await client.async_validate_credentials()
    return {
        "title": f"Honda Mapit ({data[CONF_EMAIL]})",
        CONF_ACCOUNT_ID: account["id"],
    }


class HondaMapitConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Honda Mapit."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except MapitAuthError:
                errors["base"] = "invalid_auth"
            except MapitConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCOUNT_ID: info[CONF_ACCOUNT_ID],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        entry = self._reauth_entry

        if user_input is not None and entry is not None:
            merged_input = {
                CONF_EMAIL: entry.data[CONF_EMAIL],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                info = await validate_input(self.hass, merged_input)
            except MapitAuthError:
                errors["base"] = "invalid_auth"
            except MapitConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCOUNT_ID: info[CONF_ACCOUNT_ID],
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
