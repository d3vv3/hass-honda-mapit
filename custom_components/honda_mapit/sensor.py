"""Sensor platform for Honda Mapit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import parse_iso_datetime
from .entity import HondaMapitEntityDescriptionMixin, HondaMapitVehicleEntity
from .const import DOMAIN
from .coordinator import HondaMapitCoordinator


def battery_icon(device_state: dict[str, Any]) -> str:
    """Return an MDI icon for the current battery state."""

    level = device_state.get("battery")
    if level is None:
        return "mdi:battery-unknown"

    try:
        percentage = max(0, min(100, int(level)))
    except (TypeError, ValueError):
        return "mdi:battery-unknown"

    charging = (
        bool(device_state.get("charging"))
        or "CHARG" in str(device_state.get("status") or "").upper()
    )

    if charging:
        if percentage >= 100:
            return "mdi:battery-charging-100"
        if percentage <= 10:
            return "mdi:battery-charging-10"
        rounded = min(90, ((percentage + 9) // 10) * 10)
        return f"mdi:battery-charging-{rounded}"

    if percentage >= 95:
        return "mdi:battery"
    if percentage <= 5:
        return "mdi:battery-alert-variant-outline"

    rounded = max(10, min(90, (percentage // 10) * 10))
    return f"mdi:battery-{rounded}"


def status_icon(device_state: dict[str, Any]) -> str:
    """Return an MDI icon for the current vehicle status."""

    status = str(device_state.get("status") or "").upper()
    if not status:
        return "mdi:heart-question"

    if "CHARG" in status:
        return "mdi:battery-charging"
    if status == "AT_REST":
        return "mdi:heart-pulse"
    if any(keyword in status for keyword in ("MOVE", "RID", "TRIP", "RUN")):
        return "mdi:heart"
    if any(keyword in status for keyword in ("OFFLINE", "DISCONNECT", "UNAVAILABLE")):
        return "mdi:heart-off-outline"
    if any(keyword in status for keyword in ("ERROR", "FAULT", "ALARM")):
        return "mdi:heart-flash"

    return "mdi:heart"


@dataclass(frozen=True, kw_only=True)
class HondaMapitSensorDescription(
    SensorEntityDescription, HondaMapitEntityDescriptionMixin
):
    """Honda Mapit sensor description."""


SENSORS: tuple[HondaMapitSensorDescription, ...] = (
    HondaMapitSensorDescription(
        key="battery",
        translation_key="battery",
        icon="mdi:battery",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda entity: entity.device_state.get("battery"),
    ),
    HondaMapitSensorDescription(
        key="status",
        translation_key="status",
        icon="mdi:heart",
        value_fn=lambda entity: entity.device_state.get("status"),
        attr_fn=lambda entity: {
            "model": entity.vehicle_detail.get("model")
            or entity.vehicle_summary.get("model"),
            "plan": entity.vehicle_detail.get("productPlanName"),
            "registration_number": entity.vehicle_summary.get("registrationNumber"),
        },
    ),
    HondaMapitSensorDescription(
        key="odometer",
        translation_key="odometer",
        icon="mdi:counter",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda entity: entity.vehicle_detail.get("km"),
    ),
    HondaMapitSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        icon="mdi:update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda entity: entity.ms_to_datetime(
            entity.device_state.get("lastTs")
        ),
    ),
    HondaMapitSensorDescription(
        key="last_location",
        translation_key="last_location",
        icon="mdi:map-marker-radius",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda entity: entity.ms_to_datetime(
            entity.device_state.get("lastCoordTs")
        ),
    ),
    HondaMapitSensorDescription(
        key="route_count",
        translation_key="route_count",
        icon="mdi:routes",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda entity: len(entity.routes),
    ),
    HondaMapitSensorDescription(
        key="route_days",
        translation_key="route_days",
        icon="mdi:calendar-month",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda entity: entity.route_days(),
    ),
    HondaMapitSensorDescription(
        key="last_route_started",
        translation_key="last_route_started",
        icon="mdi:timer-play",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda entity: parse_iso_datetime(
            entity.latest_route.get("startedAt") if entity.latest_route else None
        ),
    ),
    HondaMapitSensorDescription(
        key="last_route_distance",
        translation_key="last_route_distance",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda entity: (
            round(entity.latest_route.get("distance", 0) / 1000, 1)
            if entity.latest_route and entity.latest_route.get("distance") is not None
            else None
        ),
        attr_fn=lambda entity: {
            "route_id": entity.latest_route.get("id") if entity.latest_route else None,
            "avg_speed_kmh": entity.latest_route.get("avgSpeed")
            if entity.latest_route
            else None,
            "max_speed_kmh": entity.latest_route.get("maxSpeed")
            if entity.latest_route
            else None,
        },
    ),
    HondaMapitSensorDescription(
        key="last_route_duration",
        translation_key="last_route_duration",
        icon="mdi:timer-sand",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda entity: entity.route_duration_minutes(entity.latest_route),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: HondaMapitCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    entities: list[HondaMapitSensor] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vehicle_id = vehicle["id"]
        entities.extend(
            HondaMapitSensor(coordinator, vehicle_id, description)
            for description in SENSORS
        )
    async_add_entities(entities)


class HondaMapitSensor(HondaMapitVehicleEntity, SensorEntity):
    """Representation of a Honda Mapit sensor."""

    entity_description: HondaMapitSensorDescription

    def __init__(
        self,
        coordinator: HondaMapitCoordinator,
        vehicle_id: str,
        description: HondaMapitSensorDescription,
    ) -> None:
        super().__init__(coordinator, vehicle_id)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self)

    @property
    def icon(self) -> str | None:
        if self.entity_description.key == "battery":
            return battery_icon(self.device_state)
        if self.entity_description.key == "status":
            return status_icon(self.device_state)
        return self.entity_description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attr_fn is None:
            return None
        return {
            key: value
            for key, value in self.entity_description.attr_fn(self).items()
            if value is not None
        }
