# Honda Mapit for Home Assistant

<p align="center">
    <img src="custom_components/honda_mapit/brand/logo.png" alt="Mapit Logo" width="200"/>
    <br/>
    <b>Custom Home Assistant integration for Honda Mapit motorcycles.</b>
    <br/>
    <a href="https://app.mapit.me/">website</a>
</p>


Current features:

- Email/password config flow
- Polling refresh for vehicle snapshot data
- Websocket live updates for latest device state when available
- Battery, status, odometer, last seen, last location, route count, route days
- GPS device tracker from the latest reported location
- Route detail service
- GPX export service generated from route GeoJSON

## Installation

### HACS

1. Open HACS in Home Assistant.
1. On the top right corner, open the three-dot menu and select "Custom repositories".
1. Add the repository URL `https://github.com/d3vv3/hass-honda-mapit`
1. Set the Type to `Integration`.

### Manually

1. Copy `custom_components/honda_mapit/` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.
3. Open Settings -> Devices & Services -> Add Integration.
4. Search for `Honda Mapit`.
5. Enter your Honda Mapit email and password.

## Entities

For each vehicle, the integration creates:

- Sensors for battery, status, odometer, timestamps, route count, route days, and latest route metrics
- A binary sensor that indicates whether the vehicle appears to be moving
- A device tracker using the latest location snapshot

## Services

### `honda_mapit.get_route_detail`

Returns the raw route detail payload for a given route ID.

Example service data:

```yaml
route_id: rt-1234567890
```

### `honda_mapit.export_route_gpx`

Returns a generated GPX string for a given route ID.

Example service data:

```yaml
route_id: rt-1234567890
```

If you configure multiple Honda Mapit accounts, you can also pass `config_entry_id`.

## Notes

- This version uses polling plus websocket live updates for device state.
- Websocket messages can carry direct `lat`/`lng` coordinates, which are used immediately for the tracker when present.
- Cognito/API endpoints are auto-discovered from the public Mapit frontend bundle, with built-in fallback defaults.
- Brand images are bundled locally for Home Assistant 2026.3+.
- Route history is cached for 6 hours to avoid repeatedly downloading large route payloads.
