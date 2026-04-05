"""API client for Honda Mapit."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import aiohttp

from .const import (
    AUTH_REFRESH_MARGIN,
    DEFAULT_COGNITO_APP_CLIENT_ID,
    DEFAULT_COGNITO_IDENTITY_POOL_ID,
    DEFAULT_COGNITO_REGION,
    DEFAULT_COGNITO_USER_POOL_ID,
    DEFAULT_CORE_API_URL,
    DEFAULT_DEVICESTATE_WS_URL,
    DEFAULT_GEO_API_URL,
    MAPIT_APP_URL,
    ROUTE_CACHE_INTERVAL,
    WEBSOCKET_HEARTBEAT,
)

_LOGGER = logging.getLogger(__name__)

_BUNDLE_PATH_RE = re.compile(r'(?P<path>/assets/index\.[^"\']+\.js)')
_DISCOVERY_PATTERNS = {
    "identity_pool_id": re.compile(r'VITE_COGNITO_IDENTITY_POOL_ID:"(?P<value>[^"]+)"'),
    "user_pool_id": re.compile(r'VITE_COGNITO_USER_POOL_ID:"(?P<value>[^"]+)"'),
    "app_client_id": re.compile(r'VITE_COGNITO_CLIENT_ID:"(?P<value>[^"]+)"'),
    "core_api_url": re.compile(r'VITE_MAPIT_CORE_API:"(?P<value>[^"]+)"'),
    "geo_api_url": re.compile(r'VITE_MAPIT_GEO_API:"(?P<value>[^"]+)"'),
    "region": re.compile(r'Auth:\{region:"(?P<value>[^"]+)"'),
}


class MapitError(Exception):
    """Base error for Honda Mapit."""


class MapitAuthError(MapitError):
    """Authentication failed."""


class MapitConnectionError(MapitError):
    """Connection to Honda Mapit failed."""


@dataclass(slots=True)
class CognitoTokens:
    """Cognito tokens."""

    access_token: str
    id_token: str
    refresh_token: str | None
    expires_at: datetime


@dataclass(slots=True)
class AwsCredentials:
    """Temporary AWS credentials."""

    access_key_id: str
    secret_key: str
    session_token: str
    expiration: datetime


@dataclass(slots=True)
class MapitRuntimeConfig:
    """Dynamic runtime config for Honda Mapit."""

    region: str
    user_pool_id: str
    app_client_id: str
    identity_pool_id: str
    core_api_url: str
    geo_api_url: str
    devicestate_ws_url: str
    source: str = "fallback"

    @property
    def cognito_logins_key(self) -> str:
        return f"cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def cognito_idp_url(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/"

    @property
    def cognito_identity_url(self) -> str:
        return f"https://cognito-identity.{self.region}.amazonaws.com/"


def default_runtime_config() -> MapitRuntimeConfig:
    """Return the built-in runtime config fallback."""
    return MapitRuntimeConfig(
        region=DEFAULT_COGNITO_REGION,
        user_pool_id=DEFAULT_COGNITO_USER_POOL_ID,
        app_client_id=DEFAULT_COGNITO_APP_CLIENT_ID,
        identity_pool_id=DEFAULT_COGNITO_IDENTITY_POOL_ID,
        core_api_url=DEFAULT_CORE_API_URL,
        geo_api_url=DEFAULT_GEO_API_URL,
        devicestate_ws_url=DEFAULT_DEVICESTATE_WS_URL,
        source="fallback",
    )


class MapitApiClient:
    """Honda Mapit HTTP API client."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._runtime = default_runtime_config()
        self._account: dict[str, Any] | None = None
        self._tokens: CognitoTokens | None = None
        self._aws_credentials: AwsCredentials | None = None
        self._identity_id: str | None = None
        self._account_id: str | None = None
        self._route_cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}

    @property
    def runtime_config(self) -> MapitRuntimeConfig:
        """Return the currently active runtime configuration."""
        return self._runtime

    async def async_validate_credentials(self) -> dict[str, Any]:
        """Validate credentials and return the account payload."""
        await self._ensure_authenticated(force_login=True)
        return await self.async_get_account()

    async def async_discover_runtime_config(
        self, *, force: bool = False
    ) -> MapitRuntimeConfig:
        """Discover runtime configuration from the public Mapit frontend."""
        if self._runtime.source == "discovered" and not force:
            return self._runtime

        try:
            html_text = await self._fetch_text(MAPIT_APP_URL)
            bundle_url = extract_bundle_url(html_text)
            if bundle_url is None:
                raise MapitConnectionError("Could not locate Mapit frontend bundle")

            bundle_text = await self._fetch_text(bundle_url)
            discovered = extract_runtime_config(bundle_text)
        except MapitError as err:
            if force:
                raise
            _LOGGER.debug("Falling back to built-in Mapit config: %s", err)
            self._runtime = default_runtime_config()
        else:
            self._runtime = discovered
            _LOGGER.debug(
                "Discovered Mapit runtime config from frontend bundle %s", bundle_url
            )

        return self._runtime

    @classmethod
    async def async_probe_runtime_config(
        cls, session: aiohttp.ClientSession
    ) -> MapitRuntimeConfig:
        """Discover runtime configuration without valid account credentials."""
        client = cls(session, "", "")
        return await client.async_discover_runtime_config(force=True)

    async def async_get_snapshot(self) -> dict[str, Any]:
        """Fetch the current integration snapshot."""
        account = await self.async_get_account()
        summary = await self.async_get_account_summary(account["id"])
        vehicles: list[dict[str, Any]] = summary.get("vehicles", [])

        vehicle_details: dict[str, dict[str, Any]] = {}
        routes: dict[str, list[dict[str, Any]]] = {}

        for vehicle in vehicles:
            vehicle_id = vehicle["id"]
            vehicle_details[vehicle_id] = await self.async_get_vehicle_detail(
                vehicle_id
            )
            routes[vehicle_id] = await self.async_get_routes(vehicle_id)

        return {
            "account": account,
            "summary": summary,
            "vehicles": vehicles,
            "vehicle_details": vehicle_details,
            "routes": routes,
        }

    async def _fetch_text(self, url: str) -> str:
        """Fetch text content from a URL."""
        try:
            async with self._session.get(url) as response:
                text = await response.text()
        except aiohttp.ClientError as err:
            raise MapitConnectionError(str(err)) from err

        if response.status >= 400:
            raise MapitConnectionError(f"HTTP {response.status} while fetching {url}")

        return text

    async def async_get_account(self) -> dict[str, Any]:
        """Fetch the account that matches the configured email."""
        if self._account is not None:
            return self._account

        accounts = await self._mapit_request(
            "GET",
            f"{self._runtime.core_api_url}/v1/accounts",
            params={"email": self._email},
        )
        if not isinstance(accounts, list) or not accounts:
            raise MapitAuthError("No account returned for configured email")

        account = accounts[0]
        self._account_id = account.get("id")
        self._account = account
        return account

    async def async_get_account_summary(self, account_id: str) -> dict[str, Any]:
        """Fetch the account summary payload."""
        return await self._mapit_request(
            "GET", f"{self._runtime.core_api_url}/v1/accounts/{account_id}/summary"
        )

    async def async_get_vehicle_detail(self, vehicle_id: str) -> dict[str, Any]:
        """Fetch the vehicle detail payload."""
        return await self._mapit_request(
            "GET", f"{self._runtime.core_api_url}/v1/vehicles/{vehicle_id}"
        )

    async def async_get_routes(self, vehicle_id: str) -> list[dict[str, Any]]:
        """Fetch cached route summaries for a vehicle."""
        now = datetime.now(UTC)
        cached = self._route_cache.get(vehicle_id)
        if cached is not None and now - cached[0] < ROUTE_CACHE_INTERVAL:
            return cached[1]

        payload = await self._mapit_request(
            "GET",
            f"{self._runtime.geo_api_url}/v1/routes",
            params={"vehicleId": vehicle_id},
        )
        routes = payload.get("data", []) if isinstance(payload, dict) else []
        routes.sort(key=lambda item: item.get("startedAt", ""), reverse=True)
        self._route_cache[vehicle_id] = (now, routes)
        return routes

    async def async_get_route_detail(self, route_id: str) -> dict[str, Any]:
        """Fetch a route detail payload."""
        return await self._mapit_request(
            "GET", f"{self._runtime.geo_api_url}/v1/routes/{route_id}"
        )

    async def async_export_route_gpx(self, route_id: str) -> dict[str, Any]:
        """Build GPX from a route detail payload."""
        route = await self.async_get_route_detail(route_id)
        return {
            "route_id": route_id,
            "started_at": route.get("startedAt"),
            "ended_at": route.get("endedAt"),
            "distance_m": route.get("distance"),
            "gpx": build_gpx(route),
        }

    async def async_ws_connect(self, device_id: str) -> aiohttp.ClientWebSocketResponse:
        """Open the realtime websocket for a device."""
        await self._ensure_authenticated()
        assert self._tokens is not None

        try:
            return await self._session.ws_connect(
                f"{self._runtime.devicestate_ws_url}/{device_id}",
                origin="https://app.mapit.me",
                protocols=(self._tokens.id_token,),
                heartbeat=WEBSOCKET_HEARTBEAT,
            )
        except aiohttp.ClientError as err:
            raise MapitConnectionError(str(err)) from err

    async def _ensure_authenticated(self, *, force_login: bool = False) -> None:
        await self.async_discover_runtime_config()
        now = datetime.now(UTC)
        if force_login or self._tokens is None:
            await self._login()
        elif self._tokens.expires_at - AUTH_REFRESH_MARGIN <= now:
            try:
                await self._refresh_tokens()
            except MapitAuthError:
                _LOGGER.info(
                    "Honda Mapit token refresh failed; falling back to password login"
                )
                await self._login()

        if self._aws_credentials is None or self._aws_credentials.expiration <= now:
            try:
                await self._refresh_aws_credentials()
            except MapitAuthError:
                _LOGGER.info(
                    "Honda Mapit AWS credential refresh failed; retrying with fresh login"
                )
                await self._login()

    async def _login(self) -> None:
        self._account = None
        self._account_id = None
        payload = await self._cognito_idp_request(
            target="AWSCognitoIdentityProviderService.InitiateAuth",
            body={
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": self._runtime.app_client_id,
                "AuthParameters": {
                    "USERNAME": self._email,
                    "PASSWORD": self._password,
                },
                "ClientMetadata": {},
            },
        )
        auth_result = payload.get("AuthenticationResult", {})
        self._set_tokens(auth_result, auth_result.get("RefreshToken"))
        await self._refresh_aws_credentials(force_new_identity=True)

    async def _refresh_tokens(self) -> None:
        if self._tokens is None or self._tokens.refresh_token is None:
            await self._login()
            return

        payload = await self._cognito_idp_request(
            target="AWSCognitoIdentityProviderService.InitiateAuth",
            body={
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "ClientId": self._runtime.app_client_id,
                "AuthParameters": {"REFRESH_TOKEN": self._tokens.refresh_token},
                "ClientMetadata": {},
            },
        )
        auth_result = payload.get("AuthenticationResult", {})
        self._set_tokens(auth_result, self._tokens.refresh_token)
        self._aws_credentials = None

    def _set_tokens(
        self, auth_result: dict[str, Any], refresh_token: str | None
    ) -> None:
        access_token = auth_result["AccessToken"]
        id_token = auth_result["IdToken"]
        expires_in = int(auth_result.get("ExpiresIn", 3600))
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        jwt_exp = decode_jwt_exp(id_token)
        if jwt_exp is not None:
            expires_at = jwt_exp

        self._tokens = CognitoTokens(
            access_token=access_token,
            id_token=id_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

    async def _refresh_aws_credentials(
        self, *, force_new_identity: bool = False
    ) -> None:
        if self._tokens is None:
            raise MapitAuthError(
                "Cannot refresh AWS credentials without Cognito tokens"
            )

        if force_new_identity or self._identity_id is None:
            identity_payload = await self._cognito_identity_request(
                target="AWSCognitoIdentityService.GetId",
                body={
                    "IdentityPoolId": self._runtime.identity_pool_id,
                    "Logins": {self._runtime.cognito_logins_key: self._tokens.id_token},
                },
            )
            self._identity_id = identity_payload["IdentityId"]

        credentials_payload = await self._cognito_identity_request(
            target="AWSCognitoIdentityService.GetCredentialsForIdentity",
            body={
                "IdentityId": self._identity_id,
                "Logins": {self._runtime.cognito_logins_key: self._tokens.id_token},
            },
        )
        credentials = credentials_payload["Credentials"]
        expiration = parse_aws_timestamp(credentials["Expiration"])
        self._aws_credentials = AwsCredentials(
            access_key_id=credentials["AccessKeyId"],
            secret_key=credentials["SecretKey"],
            session_token=credentials["SessionToken"],
            expiration=expiration,
        )

    async def _cognito_idp_request(
        self, target: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._aws_json_request(
            url=self._runtime.cognito_idp_url,
            target=target,
            body=body,
        )

    async def _cognito_identity_request(
        self, target: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._aws_json_request(
            url=self._runtime.cognito_identity_url,
            target=target,
            body=body,
        )

    async def _aws_json_request(
        self, *, url: str, target: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": target,
            "X-Amz-User-Agent": "aws-amplify/5.0.4 js",
            "Accept": "*/*",
            "Origin": "https://app.mapit.me",
            "Referer": "https://app.mapit.me/",
        }
        payload = json.dumps(body)

        try:
            async with self._session.post(
                url, data=payload, headers=headers
            ) as response:
                text = await response.text()
        except aiohttp.ClientError as err:
            raise MapitConnectionError(str(err)) from err

        if response.status >= 400:
            try:
                error_payload = json.loads(text)
            except json.JSONDecodeError:
                error_payload = {"message": text}
            error_name = error_payload.get("__type", "")
            message = (
                error_payload.get("message") or error_payload.get("Message") or text
            )
            if "NotAuthorized" in error_name or response.status in {400, 401, 403}:
                raise MapitAuthError(message)
            raise MapitConnectionError(message)

        return json.loads(text)

    async def _mapit_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        retry_on_auth_error: bool = True,
    ) -> Any:
        await self._ensure_authenticated()
        assert self._tokens is not None
        assert self._aws_credentials is not None

        headers = self._build_mapit_headers(method, url, params=params)

        try:
            async with self._session.request(
                method,
                url,
                params=params,
                headers=headers,
            ) as response:
                text = await response.text()
        except aiohttp.ClientError as err:
            raise MapitConnectionError(str(err)) from err

        if response.status in {401, 403} and retry_on_auth_error:
            _LOGGER.debug("Refreshing auth after %s from %s", response.status, url)
            await self._login()
            return await self._mapit_request(
                method,
                url,
                params=params,
                retry_on_auth_error=False,
            )

        if response.status >= 400:
            message = text
            try:
                payload = json.loads(text)
                message = payload.get("message") or payload.get("Message") or text
            except json.JSONDecodeError:
                payload = None
            if response.status in {401, 403}:
                raise MapitAuthError(message)
            raise MapitConnectionError(message)

        if not text:
            return None

        return json.loads(text)

    def _build_mapit_headers(
        self, method: str, url: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, str]:
        assert self._tokens is not None
        assert self._aws_credentials is not None

        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        parsed_url = urlsplit(url)
        canonical_uri = quote(parsed_url.path or "/", safe="/-_.~")
        canonical_querystring = canonical_query(params or {})

        canonical_headers = (
            f"accept:application/json\n"
            f"host:{parsed_url.netloc}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "accept;host;x-amz-date"
        payload_hash = hashlib.sha256(b"").hexdigest()
        canonical_request = "\n".join(
            [
                method.upper(),
                canonical_uri,
                canonical_querystring,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )

        credential_scope = (
            f"{date_stamp}/{self._runtime.region}/execute-api/aws4_request"
        )
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )

        signing_key = get_signature_key(
            self._aws_credentials.secret_key,
            date_stamp,
            self._runtime.region,
            "execute-api",
        )
        signature = hmac.new(
            signing_key,
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self._aws_credentials.access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        return {
            "Accept": "application/json",
            "Authorization": authorization,
            "Origin": "https://app.mapit.me",
            "Referer": "https://app.mapit.me/",
            "X-Amz-Date": amz_date,
            "X-Amz-Security-Token": self._aws_credentials.session_token,
            "X-Id-Token": self._tokens.id_token,
        }


def canonical_query(params: dict[str, Any]) -> str:
    """Build a canonical AWS query string."""
    if not params:
        return ""

    items: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            for item in value:
                items.append((str(key), str(item)))
        else:
            items.append((str(key), str(value)))

    items.sort()
    return "&".join(
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}" for key, value in items
    )


def sign(key: bytes, msg: str) -> bytes:
    """Create an HMAC signature."""
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def get_signature_key(
    key: str, date_stamp: str, region_name: str, service_name: str
) -> bytes:
    """Build an AWS V4 signing key."""
    k_date = sign(("AWS4" + key).encode(), date_stamp)
    k_region = sign(k_date, region_name)
    k_service = sign(k_region, service_name)
    return sign(k_service, "aws4_request")


def decode_jwt_exp(token: str) -> datetime | None:
    """Decode the exp claim from a JWT without verification."""
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
    except (IndexError, KeyError, ValueError, json.JSONDecodeError):
        return None


def parse_aws_timestamp(value: str | int | float) -> datetime:
    """Parse an AWS timestamp payload value."""
    if isinstance(value, str):
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    return datetime.fromtimestamp(float(value), tz=UTC)


def parse_mapit_point(hex_value: str | None) -> tuple[float, float] | None:
    """Parse an EWKB point string into latitude/longitude."""
    if not hex_value:
        return None

    try:
        data = bytes.fromhex(hex_value)
        byte_order = "<" if data[0] == 1 else ">"
        geom_type = struct.unpack(f"{byte_order}I", data[1:5])[0]
        has_srid = bool(geom_type & 0x20000000)
        geom_type &= 0xFFFF
        if geom_type != 1:
            return None

        offset = 5
        if has_srid:
            offset += 4

        lon = struct.unpack(f"{byte_order}d", data[offset : offset + 8])[0]
        lat = struct.unpack(f"{byte_order}d", data[offset + 8 : offset + 16])[0]
    except (ValueError, struct.error):
        return None

    return lat, lon


def extract_device_coordinates(
    state: dict[str, Any] | None,
) -> tuple[float, float] | None:
    """Extract latitude/longitude from websocket or REST device state."""
    if not state:
        return None

    lat = state.get("lat")
    lng = state.get("lng")
    if lat is not None and lng is not None:
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            return None

    return parse_mapit_point(state.get("location"))


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO datetime string."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_bundle_url(index_html: str) -> str | None:
    """Extract the hashed frontend bundle URL from the app HTML."""
    match = _BUNDLE_PATH_RE.search(index_html)
    if match is None:
        return None
    return urljoin(MAPIT_APP_URL, html.unescape(match.group("path")))


def extract_runtime_config(bundle_text: str) -> MapitRuntimeConfig:
    """Extract runtime config values from the frontend bundle."""
    values: dict[str, str] = {}
    for key, pattern in _DISCOVERY_PATTERNS.items():
        match = pattern.search(bundle_text)
        if match is None:
            raise MapitConnectionError(f"Missing Mapit runtime field: {key}")
        values[key] = match.group("value")

    return MapitRuntimeConfig(
        region=values["region"],
        user_pool_id=values["user_pool_id"],
        app_client_id=values["app_client_id"],
        identity_pool_id=values["identity_pool_id"],
        core_api_url=values["core_api_url"],
        geo_api_url=values["geo_api_url"],
        devicestate_ws_url=_derive_ws_url(values["core_api_url"]),
        source="discovered",
    )


def _derive_ws_url(core_api_url: str) -> str:
    """Derive the realtime websocket base URL from the core API URL."""
    parsed = urlsplit(core_api_url)
    host = parsed.netloc
    if host.startswith("core."):
        host = f"dsw.{host[5:]}"
    return f"wss://{host}/devicestate"


def build_gpx(route: dict[str, Any]) -> str:
    """Convert a route GeoJSON payload into GPX."""
    route_id = route.get("id", "route")
    started_at = route.get("startedAt")
    ended_at = route.get("endedAt")
    coordinates = extract_route_coordinates(route)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Home Assistant Honda Mapit" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <metadata>",
        f"    <name>{xml_escape(str(route_id))}</name>",
    ]
    if started_at:
        lines.append(f"    <time>{xml_escape(started_at)}</time>")
    lines.extend(
        [
            "  </metadata>",
            "  <trk>",
            f"    <name>{xml_escape(str(route_id))}</name>",
            "    <trkseg>",
        ]
    )
    for coordinate in coordinates:
        lon = coordinate[0]
        lat = coordinate[1]
        ele = coordinate[2] if len(coordinate) > 2 else None
        lines.append(f'      <trkpt lat="{lat}" lon="{lon}">')
        if ele is not None:
            lines.append(f"        <ele>{ele}</ele>")
        lines.append("      </trkpt>")
    lines.extend(["    </trkseg>", "  </trk>"])
    if ended_at:
        lines.append(f"  <!-- endedAt: {xml_escape(ended_at)} -->")
    lines.append("</gpx>")
    return "\n".join(lines)


def extract_route_coordinates(route: dict[str, Any]) -> list[list[float]]:
    """Extract the first LineString coordinate list from a route payload."""
    geojson = route.get("geoJSON", {})
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry", {})
        if geometry.get("type") == "LineString":
            return geometry.get("coordinates", [])
    return []


def xml_escape(value: str) -> str:
    """Escape XML text."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
