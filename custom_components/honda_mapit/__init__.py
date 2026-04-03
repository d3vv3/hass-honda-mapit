"""Honda Mapit integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import MapitApiClient
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_ROUTE_ID,
    DOMAIN,
    PLATFORMS,
    SERVICE_EXPORT_ROUTE_GPX,
    SERVICE_GET_ROUTE_DETAIL,
)
from .coordinator import HondaMapitCoordinator


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Honda Mapit component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Honda Mapit from a config entry."""
    client = MapitApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    coordinator = HondaMapitCoordinator(hass, entry, client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        raise ConfigEntryNotReady(str(err)) from err

    await coordinator.async_start_realtime()

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is not None:
        await runtime["coordinator"].async_stop_realtime()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_GET_ROUTE_DETAIL)
            hass.services.async_remove(DOMAIN, SERVICE_EXPORT_ROUTE_GPX)
    return unload_ok


async def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_GET_ROUTE_DETAIL):
        return

    async def handle_get_route_detail(call: ServiceCall) -> dict[str, Any]:
        runtime = _select_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        return await runtime["client"].async_get_route_detail(call.data[ATTR_ROUTE_ID])

    async def handle_export_route_gpx(call: ServiceCall) -> dict[str, Any]:
        runtime = _select_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        return await runtime["client"].async_export_route_gpx(call.data[ATTR_ROUTE_ID])

    route_schema = vol.Schema(
        {
            vol.Required(ATTR_ROUTE_ID): str,
            vol.Optional(ATTR_CONFIG_ENTRY_ID): str,
        }
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_ROUTE_DETAIL,
        handle_get_route_detail,
        schema=route_schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_ROUTE_GPX,
        handle_export_route_gpx,
        schema=route_schema,
        supports_response=SupportsResponse.ONLY,
    )


def _select_runtime(hass: HomeAssistant, config_entry_id: str | None) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = hass.data[DOMAIN]
    if config_entry_id:
        if config_entry_id not in entries:
            raise HomeAssistantError(
                f"Unknown Honda Mapit config entry: {config_entry_id}"
            )
        return entries[config_entry_id]
    if not entries:
        raise HomeAssistantError("Honda Mapit is not configured")
    return next(iter(entries.values()))
