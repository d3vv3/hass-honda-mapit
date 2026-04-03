"""Shared entities for Honda Mapit."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import parse_iso_datetime
from .const import DOMAIN
from .coordinator import HondaMapitCoordinator


@dataclass(frozen=True, kw_only=True)
class HondaMapitEntityDescriptionMixin:
    """Mixin for dynamic entity descriptions."""

    value_fn: Callable[["HondaMapitVehicleEntity"], Any]
    attr_fn: Callable[["HondaMapitVehicleEntity"], dict[str, Any] | None] | None = None


class HondaMapitVehicleEntity(CoordinatorEntity[HondaMapitCoordinator]):
    """Base Honda Mapit entity tied to a vehicle."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HondaMapitCoordinator, vehicle_id: str) -> None:
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id

    @property
    def vehicle_summary(self) -> dict[str, Any]:
        for vehicle in self.coordinator.data.get("vehicles", []):
            if vehicle.get("id") == self.vehicle_id:
                return vehicle
        return {}

    @property
    def vehicle_detail(self) -> dict[str, Any]:
        return self.coordinator.data.get("vehicle_details", {}).get(self.vehicle_id, {})

    @property
    def device_state(self) -> dict[str, Any]:
        return self.vehicle_summary.get("device", {}).get("state", {})

    @property
    def routes(self) -> list[dict[str, Any]]:
        return self.coordinator.data.get("routes", {}).get(self.vehicle_id, [])

    @property
    def latest_route(self) -> dict[str, Any] | None:
        return self.routes[0] if self.routes else None

    @property
    def account(self) -> dict[str, Any]:
        return self.coordinator.data.get("account", {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.vehicle_summary)

    @property
    def device_info(self) -> DeviceInfo:
        vehicle = self.vehicle_summary
        detail = self.vehicle_detail
        return DeviceInfo(
            identifiers={(DOMAIN, self.vehicle_id)},
            manufacturer=vehicle.get("product", "Honda"),
            model=detail.get("model") or vehicle.get("model"),
            name=vehicle.get("name") or detail.get("model") or self.vehicle_id,
            serial_number=detail.get("vin"),
        )

    @property
    def suggested_object_id(self) -> str | None:
        registration = self.vehicle_summary.get("registrationNumber")
        if registration:
            return registration.lower()
        return self.vehicle_id.lower()

    def route_days(self) -> int:
        return len(
            {
                route.get("startedAt", "")[:10]
                for route in self.routes
                if route.get("startedAt")
            }
        )

    @staticmethod
    def ms_to_datetime(value: int | float | None) -> datetime | None:
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
        except (TypeError, ValueError, OSError):
            return None

    @staticmethod
    def route_duration_minutes(route: dict[str, Any] | None) -> float | None:
        if not route:
            return None
        started = parse_iso_datetime(route.get("startedAt"))
        ended = parse_iso_datetime(route.get("endedAt"))
        if started is None or ended is None:
            return None
        return round((ended - started).total_seconds() / 60, 1)
