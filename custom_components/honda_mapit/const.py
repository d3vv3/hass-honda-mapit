"""Constants for the Honda Mapit integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "honda_mapit"
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
]

CONF_ACCOUNT_ID = "account_id"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=10)
ROUTE_CACHE_INTERVAL = timedelta(hours=6)
AUTH_REFRESH_MARGIN = timedelta(minutes=5)
WEBSOCKET_HEARTBEAT = 60
WEBSOCKET_RECONNECT_DELAY = timedelta(seconds=10)
WEBSOCKET_MAX_RECONNECT_DELAY = timedelta(minutes=5)

ATTR_ROUTE_ID = "route_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SERVICE_GET_ROUTE_DETAIL = "get_route_detail"
SERVICE_EXPORT_ROUTE_GPX = "export_route_gpx"

MAPIT_APP_URL = "https://app.mapit.me"
DEFAULT_COGNITO_REGION = "eu-west-1"
DEFAULT_COGNITO_USER_POOL_ID = "eu-west-1_nHd6Er8N6"
DEFAULT_COGNITO_APP_CLIENT_ID = "7fo1dt507lf6riggmprmql2mpb"
DEFAULT_COGNITO_IDENTITY_POOL_ID = "eu-west-1:a25d1457-542f-43d3-8b47-c3c60ed3675d"
DEFAULT_CORE_API_URL = "https://core.prod.mapit.me"
DEFAULT_GEO_API_URL = "https://geo.prod.mapit.me"
DEFAULT_DEVICESTATE_WS_URL = "wss://dsw.prod.mapit.me/devicestate"
