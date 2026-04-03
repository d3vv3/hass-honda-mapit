"""Device tracker platform for Honda Mapit."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import extract_device_coordinates
from .const import DOMAIN
from .coordinator import HondaMapitCoordinator
from .entity import HondaMapitVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: HondaMapitCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        HondaMapitTracker(coordinator, vehicle["id"])
        for vehicle in coordinator.data.get("vehicles", [])
    )


class HondaMapitTracker(HondaMapitVehicleEntity, TrackerEntity):
    """Tracker entity backed by the Mapit device snapshot."""

    _attr_translation_key = "location"
    _attr_icon = "mdi:motorbike"

    def __init__(self, coordinator: HondaMapitCoordinator, vehicle_id: str) -> None:
        super().__init__(coordinator, vehicle_id)
        self._attr_unique_id = f"{vehicle_id}_location"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        point = extract_device_coordinates(self.device_state)
        return point[0] if point else None

    @property
    def longitude(self) -> float | None:
        point = extract_device_coordinates(self.device_state)
        return point[1] if point else None

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        return {
            "status": self.device_state.get("status"),
            "battery": self.device_state.get("battery"),
            "device_id": self.vehicle_summary.get("device", {}).get("id"),
        }
