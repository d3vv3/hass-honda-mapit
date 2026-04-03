"""Binary sensor platform for Honda Mapit."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import HondaMapitCoordinator
from .entity import HondaMapitEntityDescriptionMixin, HondaMapitVehicleEntity


@dataclass(frozen=True, kw_only=True)
class HondaMapitBinarySensorDescription(
    BinarySensorEntityDescription, HondaMapitEntityDescriptionMixin
):
    """Honda Mapit binary sensor description."""


BINARY_SENSORS: tuple[HondaMapitBinarySensorDescription, ...] = (
    HondaMapitBinarySensorDescription(
        key="moving",
        translation_key="moving",
        value_fn=lambda entity: (
            entity.device_state.get("status") not in {None, "AT_REST"}
        ),
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
    entities: list[HondaMapitBinarySensor] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vehicle_id = vehicle["id"]
        entities.extend(
            HondaMapitBinarySensor(coordinator, vehicle_id, description)
            for description in BINARY_SENSORS
        )
    async_add_entities(entities)


class HondaMapitBinarySensor(HondaMapitVehicleEntity, BinarySensorEntity):
    """Representation of a Honda Mapit binary sensor."""

    entity_description: HondaMapitBinarySensorDescription

    def __init__(
        self,
        coordinator: HondaMapitCoordinator,
        vehicle_id: str,
        description: HondaMapitBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, vehicle_id)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        return bool(self.entity_description.value_fn(self))
