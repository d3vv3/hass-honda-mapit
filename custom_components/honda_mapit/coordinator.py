"""Coordinator for Honda Mapit data."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
import json
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MapitApiClient, MapitAuthError, MapitConnectionError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    WEBSOCKET_MAX_RECONNECT_DELAY,
    WEBSOCKET_RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)


class HondaMapitCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage Honda Mapit data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: MapitApiClient,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.config_entry = config_entry
        self.client = client
        self._ws_tasks: dict[str, asyncio.Task[None]] = {}
        self._ws_stop_event = asyncio.Event()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            snapshot = await self.client.async_get_snapshot()
        except MapitAuthError as err:
            raise ConfigEntryAuthFailed from err
        except MapitConnectionError as err:
            raise UpdateFailed(str(err)) from err

        snapshot = _merge_snapshot_preserving_device_state(self.data, snapshot)
        await self._async_sync_ws_tasks(snapshot)
        return snapshot

    async def async_start_realtime(self) -> None:
        """Start websocket listeners for known devices."""
        self._ws_stop_event.clear()
        if self.data:
            await self._async_sync_ws_tasks(self.data)

    async def async_stop_realtime(self) -> None:
        """Stop all websocket listeners."""
        self._ws_stop_event.set()
        tasks = list(self._ws_tasks.values())
        self._ws_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_sync_ws_tasks(self, snapshot: dict[str, Any]) -> None:
        desired_devices = {
            vehicle.get("device", {}).get("id")
            for vehicle in snapshot.get("vehicles", [])
            if vehicle.get("device", {}).get("id")
        }

        for device_id in list(self._ws_tasks):
            if device_id not in desired_devices:
                task = self._ws_tasks.pop(device_id)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        for device_id in desired_devices:
            if device_id in self._ws_tasks or self._ws_stop_event.is_set():
                continue
            self._ws_tasks[device_id] = asyncio.create_task(
                self._async_device_listener(device_id),
                name=f"{DOMAIN}_ws_{device_id}",
            )

    async def _async_device_listener(self, device_id: str) -> None:
        delay = WEBSOCKET_RECONNECT_DELAY.total_seconds()
        max_delay = WEBSOCKET_MAX_RECONNECT_DELAY.total_seconds()

        try:
            while not self._ws_stop_event.is_set():
                websocket: aiohttp.ClientWebSocketResponse | None = None
                try:
                    websocket = await self.client.async_ws_connect(device_id)
                    delay = WEBSOCKET_RECONNECT_DELAY.total_seconds()

                    async for message in websocket:
                        if self._ws_stop_event.is_set():
                            break

                        payload = _decode_ws_message(message)
                        if payload is None:
                            continue

                        state = _extract_device_state(payload)
                        if state is None:
                            _LOGGER.debug(
                                "Ignoring websocket payload for %s without device state keys",
                                device_id,
                            )
                            continue

                        _LOGGER.debug(
                            "Honda Mapit websocket update for %s: %s",
                            device_id,
                            _summarize_device_state_for_log(state),
                        )

                        self.async_set_updated_data(
                            _merge_device_state(self.data, device_id, state)
                        )

                    if websocket.close_code not in {None, aiohttp.WSCloseCode.OK}:
                        _LOGGER.debug(
                            "Honda Mapit websocket for %s closed with code %s",
                            device_id,
                            websocket.close_code,
                        )
                except asyncio.CancelledError:
                    raise
                except MapitAuthError as err:
                    _LOGGER.warning(
                        "Honda Mapit websocket auth failed for %s: %s", device_id, err
                    )
                    self.hass.async_create_task(self._async_recover_auth())
                    break
                except (MapitConnectionError, aiohttp.ClientError, TimeoutError) as err:
                    _LOGGER.debug(
                        "Honda Mapit websocket for %s failed: %s", device_id, err
                    )
                finally:
                    if websocket is not None and not websocket.closed:
                        await websocket.close()

                if self._ws_stop_event.is_set():
                    break

                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
        finally:
            task = self._ws_tasks.get(device_id)
            if task is asyncio.current_task():
                self._ws_tasks.pop(device_id, None)

    async def _async_recover_auth(self) -> None:
        """Trigger a coordinator refresh after websocket auth failure."""
        try:
            await self.async_request_refresh()
        except ConfigEntryAuthFailed:
            _LOGGER.warning(
                "Honda Mapit automatic auth recovery failed; starting Home Assistant reauth"
            )
            self.config_entry.async_start_reauth(self.hass)


def _decode_ws_message(message: aiohttp.WSMessage) -> Any | None:
    """Decode a websocket message into a Python payload."""
    if message.type == aiohttp.WSMsgType.TEXT:
        try:
            return json.loads(message.data)
        except json.JSONDecodeError:
            return None

    if message.type == aiohttp.WSMsgType.BINARY:
        try:
            return json.loads(message.data.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    return None


def _extract_device_state(payload: Any) -> dict[str, Any] | None:
    """Extract a device-state-like mapping from a websocket payload."""
    if not isinstance(payload, Mapping):
        return None

    if _looks_like_device_state(payload):
        state = dict(payload)
        if "deviceId" not in state and "id" in state:
            state["deviceId"] = state["id"]
        return state

    for key in ("state", "data", "payload", "deviceState", "message"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            state = _extract_device_state(nested)
            if state is not None:
                return state

    return None


def _looks_like_device_state(payload: Mapping[str, Any]) -> bool:
    """Return whether the payload looks like a Mapit device state."""
    keys = {
        "id",
        "battery",
        "status",
        "lastTs",
        "lastCoordTs",
        "location",
        "lat",
        "lng",
        "deviceId",
    }
    return bool(keys.intersection(payload.keys()))


def _merge_device_state(
    current: dict[str, Any] | None, device_id: str, state: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge websocket state into the current coordinator snapshot."""
    snapshot = deepcopy(current or {})
    vehicles = snapshot.get("vehicles", [])
    for vehicle in vehicles:
        device = vehicle.get("device", {})
        if device.get("id") != device_id:
            continue

        merged_state = dict(device.get("state", {}))
        merged_state.update(state)
        merged_state.setdefault("deviceId", device_id)
        device["state"] = merged_state
        return snapshot

    return snapshot


def _merge_snapshot_preserving_device_state(
    current: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Preserve last known device-state values when refresh data is null/missing."""

    if not current:
        return incoming

    snapshot = deepcopy(incoming)
    previous_vehicles = {
        vehicle.get("id"): vehicle
        for vehicle in current.get("vehicles", [])
        if vehicle.get("id")
    }

    for vehicle in snapshot.get("vehicles", []):
        previous_vehicle = previous_vehicles.get(vehicle.get("id"))
        if previous_vehicle is None:
            continue

        previous_device = previous_vehicle.get("device") or {}
        current_device = vehicle.get("device") or {}

        previous_state = previous_device.get("state") or {}
        current_state = dict(current_device.get("state") or {})

        if not previous_state:
            continue

        for key, value in previous_state.items():
            if key not in current_state or current_state[key] is None:
                current_state[key] = value

        current_device["state"] = current_state
        vehicle["device"] = current_device

    return snapshot


def _summarize_device_state_for_log(state: Mapping[str, Any]) -> dict[str, Any]:
    """Build a safe debug summary for websocket state."""
    return {
        "id": state.get("id") or state.get("deviceId"),
        "updatedAt": state.get("updatedAt"),
        "battery": state.get("battery"),
        "status": state.get("status"),
        "lastTs": state.get("lastTs"),
        "lastCoordTs": state.get("lastCoordTs"),
        "has_coords": state.get("lat") is not None and state.get("lng") is not None,
        "keys": sorted(state.keys()),
    }
