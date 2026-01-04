# traffic_generator.py

import asyncio
import aiohttp
import json
import random
import time
import psutil
from typing import List, Dict, Any, Optional
import logging
import re
from pydantic import (
    BaseModel,
    Field,
    validator,
    RootModel,
    model_validator,
)  # Added model_validator
from ipaddress import ip_address, AddressValueError
from urllib.parse import urlparse, urlunparse, quote
from collections import deque

# Set up a dedicated logger for the traffic generator
logger = logging.getLogger("Traffic Generator")
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
logger.propagate = True

# -------------
# Export so container_control can import them
# -------------
__all__ = ["asyncio", "logger", "StartRequest", "TrafficGenerator", "Metrics"]


# ---------------------------
# Configuration Models
# ---------------------------
class CredentialHeaders(BaseModel):
    Authorization: Optional[str] = None


class FormData(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None


class JsonBody(RootModel):
    root: Dict[str, Any]


class Credentials(BaseModel):
    header: Optional[CredentialHeaders] = None
    body_params: Optional[FormData] = None
    json_body: Optional[Dict[str, Any]] = None


class AuthConfig(BaseModel):
    auth_method: str
    auth_path: str
    auth_type: str
    credentials: Credentials

    @validator("auth_type")
    def validate_auth_type(cls, v):
        allowed = [
            "basic",
            "bearer",
            "body_params",
            "json_body",
            "query_params",
            "custom_header",
        ]
        # Ensure auth_type is not empty when AuthConfig is being validated
        # (which should only happen if has_auth is true, due to SiteMap validator)
        if not v:
            raise ValueError("auth_type cannot be empty when authentication is enabled")
        if v.lower() not in allowed:
            raise ValueError(f"auth_type must be one of {allowed}")
        return v.lower()


class PathDefinition(BaseModel):
    method: str
    paths: List[str]
    body: Optional[str] = None
    traffic_type: str

    @validator("traffic_type")
    def validate_traffic_type(cls, v):
        allowed = ["web", "api"]
        if v.lower() not in allowed:
            raise ValueError("traffic_type must be either 'web' or 'api'")
        return v.lower()

    @validator("method")
    def validate_method(cls, v):
        allowed_methods = ["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"]
        if v.upper() not in allowed_methods:
            raise ValueError(f"method must be one of {allowed_methods}")
        return v.upper()


class HeaderOverride(BaseModel):
    paths: Optional[List[str]] = None
    headers: Optional[Dict[str, str]] = None


class VariableDefinition(BaseModel):
    type: str
    value: List[Any]

    @validator("type")
    def validate_variable_type(cls, v):
        allowed = ["list", "range"]
        if v.lower() not in allowed:
            raise ValueError(f"Variable type must be one of {allowed}")
        return v.lower()


class SiteMap(BaseModel):
    has_auth: bool
    paths: List[PathDefinition]
    paths_auth_req: Optional[List[PathDefinition]] = []
    auth: Optional[AuthConfig] = None  # Stays Optional
    path_headers_override: Optional[HeaderOverride] = None
    global_headers: Dict[str, str] = {}
    variables: Dict[str, VariableDefinition] = {}

    @model_validator(mode="before")
    @classmethod
    def check_auth_logic(cls, data: Any) -> Any:
        """
        Ensures that if has_auth is False, the auth field is ignored (set to None),
        preventing validation errors on AuthConfig.
        If has_auth is True, ensures the auth field is present and lets
        Pydantic validate it against AuthConfig.
        """
        if isinstance(data, dict):
            has_auth = data.get("has_auth")  # Get the value of has_auth
            auth_config = data.get("auth")

            # Explicitly check if has_auth is present, as it's mandatory for this logic
            if has_auth is None:
                raise ValueError("'has_auth' field is required in sitemap")

            if not has_auth:
                # If has_auth is False, force auth to None, regardless of input.
                # This prevents validation of the potentially empty/invalid auth object.
                data["auth"] = None
            else:
                # If has_auth is True, auth config MUST be present.
                if not auth_config or not isinstance(auth_config, dict):
                    raise ValueError(
                        "auth configuration object is required when has_auth is True"
                    )
                # We can add checks for specific required fields inside auth here if needed,
                # but AuthConfig's own validators should handle it primarily.
                # Example: Ensure auth_type isn't empty *before* AuthConfig validation runs.
                if not auth_config.get("auth_type"):
                    raise ValueError(
                        "auth_type is required and cannot be empty within auth configuration when has_auth is True"
                    )
                if not auth_config.get("auth_method"):
                    raise ValueError(
                        "auth_method is required and cannot be empty within auth configuration when has_auth is True"
                    )
                if not auth_config.get("auth_path"):
                    raise ValueError(
                        "auth_path is required and cannot be empty within auth configuration when has_auth is True"
                    )

        return data


def alias_generator(field_name: str) -> str:
    mapping = {
        "traffic_target_url": "Traffic Generator URL",
        "traffic_target_dns_override": "Traffic Generator DNS Override",
        "xff_header_name": "XFF Header Name",
        "rate_limit": "Rate Limit",
        "sim_users": "Simulated Users",
        "min_session_length": "Minimum Session Length",
        "max_session_length": "Maximum Session Length",
        "debug": "Debug",
    }
    return mapping.get(field_name, field_name)


class ContainerConfig(BaseModel):
    traffic_target_url: str = Field(..., alias="Traffic Generator URL")
    traffic_target_dns_override: Optional[str] = Field(
        None, alias="Traffic Generator DNS Override"
    )
    xff_header_name: str = Field(..., alias="XFF Header Name")
    rate_limit: int = Field(..., alias="Rate Limit")
    sim_users: int = Field(..., alias="Simulated Users")
    min_session_length: int = Field(..., alias="Minimum Session Length")
    max_session_length: int = Field(..., alias="Maximum Session Length")
    debug: Optional[bool] = Field(False, alias="Debug")

    class Config:
        allow_population_by_field_name = True
        anystr_lower = False
        extra = "allow"

    @validator("traffic_target_dns_override", pre=True, always=True)
    def set_default_dns_override(cls, v):
        if v == "":
            return None
        return v

    @validator("traffic_target_dns_override")
    def validate_dns_override(cls, v):
        if v is not None:
            try:
                ip_address(v)
            except AddressValueError:
                raise ValueError(
                    f"Invalid IP address provided for Traffic Generator DNS Override: {v}"
                )
        return v


class StartRequest(BaseModel):
    config: ContainerConfig
    sitemap: SiteMap


# ---------------------------
# Metrics Tracking
# ---------------------------
class Metrics:
    """
    A rolling 1-second window of timestamps to compute instantaneous RPS.
    """

    def __init__(self):
        self.lock = asyncio.Lock()
        self.request_timestamps = deque()

    async def increment(self):
        now = time.monotonic()
        async with self.lock:
            self.request_timestamps.append(now)
            # Trim timestamps older than 1 second
            while self.request_timestamps and (now - self.request_timestamps[0]) > 1:
                self.request_timestamps.popleft()

    async def get_rps(self):
        now = time.monotonic()
        async with self.lock:
            # Trim timestamps older than 1 second before calculating
            while self.request_timestamps and (now - self.request_timestamps[0]) > 1:
                self.request_timestamps.popleft()
            return len(self.request_timestamps)


# ---------------------------
# Traffic Generator Classes
# ---------------------------
class SimulatedUser:
    def __init__(self, is_authenticated: bool, auth_token: Optional[str] = None):
        self.is_authenticated = is_authenticated
        self.auth_token = auth_token


class TrafficGenerator:
    def __init__(self, config: ContainerConfig, site_map: SiteMap, metrics: Metrics):
        self.config = config
        self.site_map = site_map  # This will now be correctly validated
        self.metrics = metrics
        self.session_semaphore = asyncio.Semaphore(self.config.rate_limit)
        self.running = False
        self.user_tasks = []  # Keep track of user tasks
        self.metrics_task = None  # Keep track of metrics task

        # Expanded and more robust user-agents lists for web
        self.user_agents_web = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0",
            "Mozilla/5.0 (iPad; CPU OS 15_5 like Mac OS X) AppleWebKit/606.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/605.1.15",
            "Mozilla/5.0 (Android 12; Mobile; rv:102.0) Gecko/102.0 Firefox/102.0",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/606.1.15 (KHTML, like Gecko) Version/15.6 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.5; rv:109.0) Gecko/20100101 Firefox/115.0",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.69",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36",
        ]

        # Expanded and more robust user-agents lists for API
        self.user_agents_api = [
            "PostmanRuntime/7.29.0",
            "Python-requests/2.27.1",
            "curl/7.79.1",
            "Go-http-client/1.1",
            "Wget/1.20.3 (linux-gnu)",
            "Apache-HttpClient/4.5.13 (Java/11.0.15)",
            "axios/0.21.1 Node.js/v14.17.0",
            "Java/1.8.0_281",
            "libwww-perl/6.31",
            "HTTPie/2.5.0",
            "okhttp/4.9.1",
            "Faraday v2.7.10",
            "Dart/2.17 (dart:io)",
            "Xamarin/3.0.0 (Xamarin.Android; Android 13; SDK 33)",
            "Insomnia/2023.5.8",
            "Nodejs-v16.16.0",
            "Dalvik/2.1.0 (Linux; U; Android 13; SM-S918B Build/TP1A.220624.014)",
            "aws-sdk-js-2.1395.0",
            "Swift-URLSession",
            "ruby rest-client/2.1.0",
        ]

        self.headers_web_options = [
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "DNT": "1",
            },
            {
                "Accept": "application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "DNT": "1",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
            },
            {
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "no-cache",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "de-DE,de;q=0.5",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
                "Sec-Fetch-User": "?1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.5",
                "Connection": "keep-alive",
                "DNT": "1",
                "Sec-Fetch-Site": "cross-site",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "it-IT,it;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja-JP,ja;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "zh-CN,zh;q=0.5",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
            },
            {
                "Accept": "application/xhtml+xml,application/xml,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.5",
                "Connection": "keep-alive",
                "DNT": "1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-AU,en;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Mode": "navigate",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "en-CA,en;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            {
                "Accept": "application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-IE,en;q=0.5",
                "Connection": "keep-alive",
                "Sec-Fetch-Site": "none",
                "Cache-Control": "max-age=0",
            },
            {
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "sv-SE,sv;q=0.5",
                "Connection": "keep-alive",
                "DNT": "1",
                "Sec-Fetch-Dest": "document",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "pt-PT,pt;q=0.5",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
                "Sec-Fetch-Mode": "navigate",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "nl-NL,nl;q=0.5",
                "Connection": "keep-alive",
                "Sec-Fetch-Site": "same-origin",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "pl-PL,pl;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            {
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.5",
                "Connection": "keep-alive",
                "X-Requested-With": "XMLHttpRequest",
            },
            {
                "Accept": "application/x-www-form-urlencoded",
                "Accept-Language": "en-GB,en;q=0.5",
                "Connection": "keep-alive",
            },
            {
                "Accept": "text/html,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.5",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "document",
            },
            {
                "Accept": "image/webp,image/*,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.5",
                "Connection": "keep-alive",
                "DNT": "1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.5",
                "Connection": "keep-alive",
                "Sec-Fetch-Mode": "navigate",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja-JP,ja;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "zh-CN,zh;q=0.5",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
            },
            {
                "Accept": "application/xhtml+xml,application/xml,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.5",
                "Connection": "keep-alive",
                "DNT": "1",
                "Sec-Fetch-Site": "cross-site",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-AU,en;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "en-CA,en;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0",
            },
            {
                "Accept": "application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-IE,en;q=0.5",
                "Connection": "keep-alive",
                "Sec-Fetch-Site": "none",
            },
            {
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "sv-SE,sv;q=0.5",
                "Connection": "keep-alive",
                "DNT": "1",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "pt-PT,pt;q=0.5",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "nl-NL,nl;q=0.5",
                "Connection": "keep-alive",
                "Sec-Fetch-Mode": "navigate",
            },
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "pl-PL,pl;q=0.5",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
            },
        ]

        self.headers_api_options = [
            {
                "Accept": "application/json",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            {
                "Accept": "application/xml",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate",
                "DNT": "1",
                "X-Requested-With": "XMLHttpRequest",
            },
            {
                "Accept": "*/*",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "X-Forwarded-Proto": "https",
            },
            {
                "Accept": "application/json, text/plain, */*",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate, br",
                "X-Real-IP": "192.0.2.123",
            },
            {
                "Accept": "application/json",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate",
                "User-Token": "randomtoken123456",
                "Forwarded": "for=198.51.100.50;proto=https",
            },
            {
                "Accept": "application/json",
                "Connection": "keep-alive",
                "Accept-Language": "en-US,en;q=0.5",
                "X-Trace-ID": "trace-56789",
                "X-Device-ID": "device-98765",
            },
            {
                "Accept": "application/vnd.api+json",
                "Connection": "keep-alive",
                "Authorization": "Bearer random_api_token",
                "X-API-Version": "2.0",
                "Accept-Encoding": "gzip, deflate, br",
            },
            {
                "Accept": "application/ld+json",
                "Connection": "keep-alive",
                "X-Correlation-ID": "some_correlation_id",
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            {
                "Accept": "text/csv",
                "Connection": "keep-alive",
                "X-Auth-Token": "some_auth_token",
                "Accept-Encoding": "gzip, deflate, br",
                "Content-Type": "text/csv",
            },
            {
                "Accept": "application/x-www-form-urlencoded",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate",
                "X-Client-Version": "1.1.0",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            {
                "Accept": "application/protobuf",
                "Connection": "keep-alive",
                "Content-Type": "application/protobuf",
                "Accept-Encoding": "gzip, deflate",
            },
            {
                "Accept": "application/octet-stream",
                "Connection": "keep-alive",
                "Content-Type": "application/octet-stream",
                "Accept-Encoding": "gzip, deflate, br",
            },
            {
                "Accept": "application/graphql",
                "Connection": "keep-alive",
                "Content-Type": "application/graphql",
                "Accept-Encoding": "gzip, deflate",
            },
            {
                "Accept": "text/plain",
                "Connection": "keep-alive",
                "Content-Type": "text/plain",
                "Accept-Encoding": "gzip, deflate, br",
            },
            {
                "Accept": "application/jwt",
                "Connection": "keep-alive",
                "Authorization": "Bearer some_jwt_token",
                "Accept-Encoding": "gzip, deflate",
            },
            {
                "Accept": "application/vnd.ms-excel",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate, br",
                "Content-Type": "application/vnd.ms-excel",
            },
            {
                "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate",
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
            {
                "Accept": "image/png",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate",
            },
            {
                "Accept": "image/jpeg",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate, br",
            },
            {
                "Accept": "image/gif",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate",
            },
            {
                "Accept": "application/pdf",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate, br",
            },
        ]

        # Parse the original target URL
        self.parsed_url = urlparse(self.config.traffic_target_url)
        self.original_host = self.parsed_url.hostname
        self.original_scheme = self.parsed_url.scheme
        self.default_port = 443 if self.original_scheme == "https" else 80
        self.target_ip = self.config.traffic_target_dns_override or self.original_host

        self.configure_logging(self.config.debug)

    def configure_logging(self, debug: bool):
        if debug:
            logger.setLevel(logging.DEBUG)
            console_handler.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)
            console_handler.setLevel(logging.INFO)

    def create_session(self) -> aiohttp.ClientSession:
        # Note: DNS override is handled by constructing the URL with the IP,
        # and setting the Host header. aiohttp's built-in resolver/connector
        # options for DNS override can be complex; this approach is often simpler.
        connector = aiohttp.TCPConnector(ssl=False)
        return aiohttp.ClientSession(connector=connector)

    async def start_generating(self):
        if self.running:
            logger.warning("Traffic generation is already running.")
            return
        self.running = True
        logger.info("Starting traffic generation tasks.")

        self.user_tasks = []
        for _ in range(self.config.sim_users):
            task = asyncio.create_task(self.simulate_user())
            self.user_tasks.append(task)

        # Only create metrics task if it doesn't exist or isn't running
        if self.metrics_task is None or self.metrics_task.done():
            self.metrics_task = asyncio.create_task(self.metrics_loop())

        # Don't await here; let it run in the background
        logger.info(
            f"Launched {len(self.user_tasks)} user simulation tasks and metrics loop."
        )

    async def stop_generating(self):
        if not self.running:
            logger.warning("Traffic generation not running.")
            return
        logger.info("Stopping traffic generation.")
        self.running = False  # Signal tasks to stop

        # Cancel all tasks
        if self.metrics_task:
            self.metrics_task.cancel()
        for task in self.user_tasks:
            task.cancel()

        # Wait for tasks to finish cancelling
        results = await asyncio.gather(
            *(self.user_tasks + ([self.metrics_task] if self.metrics_task else [])),
            return_exceptions=True,
        )

        # Log any unexpected errors during cancellation/shutdown
        for result in results:
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.error(f"Error during task shutdown: {result}")

        # Clear task lists
        self.user_tasks = []
        self.metrics_task = None
        logger.info("Traffic generation stopped.")

    async def metrics_loop(self):
        """Periodically logs the current RPS."""
        try:
            while self.running:
                await asyncio.sleep(1)
                # Ensure metrics object exists before trying to get RPS
                if self.metrics:
                    rps = await self.metrics.get_rps()
                    # Only log if running to avoid messages after stop signal
                    if self.running:
                        logger.info(f"Current RPS: {rps:.2f}")
                else:
                    # Should not happen if initialized correctly
                    logger.warning("Metrics object not available in metrics_loop.")
                    await asyncio.sleep(5)  # Avoid tight loop if metrics is missing
        except asyncio.CancelledError:
            logger.info("Metrics loop cancelled.")
        except Exception as e:
            logger.error(f"Error in metrics loop: {e}")

    async def simulate_user(self):
        """Simulates a single user session."""
        fake_ip = self.generate_random_ip()
        user_web_headers = random.choice(self.headers_web_options).copy()  # Use copy
        user_web_ua = random.choice(self.user_agents_web)
        user_api_headers = random.choice(self.headers_api_options).copy()  # Use copy
        user_api_ua = random.choice(self.user_agents_api)

        # Decide initial authentication state based on sitemap config and randomness
        # Note: site_map.auth will be None if has_auth is false, due to the validator
        initial_auth_needed = self.site_map.has_auth and self.site_map.auth is not None
        is_authenticated = initial_auth_needed and random.choice(
            [True, False]
        )  # Only try auth if needed and randomly chosen
        sim_user = SimulatedUser(is_authenticated=False)  # Start as not authenticated

        try:
            async with self.create_session() as session:
                # --- Authentication Phase (if applicable) ---
                if (
                    initial_auth_needed and is_authenticated
                ):  # Check if auth is configured AND this user attempts it
                    logger.debug(f"User {fake_ip} attempting authentication.")
                    auth_token = await self.perform_authentication(
                        session, {self.config.xff_header_name: fake_ip}
                    )
                    if auth_token:
                        sim_user.is_authenticated = True
                        sim_user.auth_token = auth_token
                        logger.info(f"User {fake_ip} authenticated successfully.")
                    else:
                        # If auth attempt fails, remain unauthenticated
                        sim_user.is_authenticated = False
                        logger.warning(f"User {fake_ip} authentication failed.")
                elif initial_auth_needed:
                    logger.debug(
                        f"User {fake_ip} starting session unauthenticated (by random choice)."
                    )
                else:
                    logger.debug(
                        f"User {fake_ip} starting session (no auth configured)."
                    )

                # --- Session Request Loop ---
                while self.running:
                    session_length_seconds = random.randint(
                        self.config.min_session_length, self.config.max_session_length
                    )
                    session_end_time = time.monotonic() + session_length_seconds
                    logger.debug(
                        f"User {fake_ip} starting session, length {session_length_seconds}s. Authenticated: {sim_user.is_authenticated}"
                    )

                    while self.running and time.monotonic() < session_end_time:
                        # --- Request Execution ---
                        await self.session_semaphore.acquire()  # Respect rate limit
                        request_performed = False
                        try:
                            # Build base headers for this request
                            base_headers = dict(self.site_map.global_headers)
                            base_headers[self.config.xff_header_name] = fake_ip

                            await self.perform_request(
                                session,
                                base_headers,  # Pass fresh base headers
                                user_web_headers,  # User-specific web headers
                                user_web_ua,  # User-specific web UA
                                user_api_headers,  # User-specific API headers
                                user_api_ua,  # User-specific API UA
                                sim_user,  # User's current auth state
                            )
                            request_performed = True
                        except asyncio.CancelledError:
                            logger.debug(f"Request cancelled for user {fake_ip}.")
                            self.session_semaphore.release()  # Release semaphore if cancelled mid-request
                            raise  # Re-raise CancelledError to stop the user task
                        except Exception as e:
                            logger.error(f"Request failed for user {fake_ip}: {e}")
                        finally:
                            # Only release semaphore if it was acquired and not already released (e.g., by cancellation)
                            # Check semaphore count if needed, but simple release is usually ok here.
                            self.session_semaphore.release()

                        # --- Post-Request ---
                        if request_performed:
                            await self.metrics.increment()

                        # Random delay between requests within a session
                        await asyncio.sleep(random.uniform(0.1, 1.0))

                    logger.debug(f"User {fake_ip} finished session segment.")
                    # Optional: Add a longer delay between sessions for a user?
                    # await asyncio.sleep(random.uniform(1.0, 5.0))

        except asyncio.CancelledError:
            logger.info(f"User simulation cancelled for IP {fake_ip}.")
        except Exception as e:
            logger.error(
                f"User simulation error for IP {fake_ip}: {e}", exc_info=True
            )  # Log traceback
        finally:
            logger.debug(f"Exiting simulate_user task for IP {fake_ip}.")

    async def perform_authentication(
        self, session: aiohttp.ClientSession, base_headers: Dict[str, str]
    ) -> Optional[str]:
        """Attempts authentication based on SiteMap config. Returns auth token or None."""
        # Auth config is guaranteed to be present if site_map.has_auth is True, due to validator
        auth = self.site_map.auth
        if not auth:  # Should not happen if has_auth is True, but safety check
            logger.error("perform_authentication called but site_map.auth is None.")
            return None

        # Construct URL, considering potential DNS override
        auth_path = self.replace_variables(auth.auth_path)  # Replace variables in path
        if self.config.traffic_target_dns_override:
            port = self.parsed_url.port or self.default_port
            # Determine netloc based on IP and port
            if (self.original_scheme == "https" and port != 443) or (
                self.original_scheme == "http" and port != 80
            ):
                netloc = f"{self.target_ip}:{port}"
            else:
                netloc = self.target_ip
            auth_url = urlunparse((self.original_scheme, netloc, auth_path, "", "", ""))
            # Add Host header for DNS override
            auth_headers = {**base_headers, "Host": self.original_host}
        else:
            auth_url = urlunparse(
                (self.original_scheme, self.parsed_url.netloc, auth_path, "", "", "")
            )
            auth_headers = base_headers.copy()  # Use copy of base headers

        auth_type = (
            auth.auth_type.lower()
        )  # Already validated to be non-empty and allowed type
        method = auth.auth_method.upper()  # Method for the auth request itself

        # Prepare data/headers based on auth type
        request_data = None
        request_json = None

        logger.debug(f"Auth attempt: Type={auth_type}, Method={method}, URL={auth_url}")

        try:
            if auth_type == "basic":
                # Assuming 'Authorization: Basic ...' is directly in credentials.header
                if auth.credentials.header and auth.credentials.header.Authorization:
                    auth_headers["Authorization"] = (
                        auth.credentials.header.Authorization
                    )
                else:
                    logger.error(
                        "Basic auth selected but no Authorization header found in credentials."
                    )
                    return None
            elif auth_type == "bearer":
                # Assuming 'Authorization: Bearer ...' is directly in credentials.header
                if auth.credentials.header and auth.credentials.header.Authorization:
                    auth_headers["Authorization"] = (
                        auth.credentials.header.Authorization
                    )
                else:
                    logger.error(
                        "Bearer auth selected but no Authorization header found in credentials."
                    )
                    return None
            elif auth_type == "body_params":
                if auth.credentials.body_params:
                    auth_headers["Content-Type"] = "application/x-www-form-urlencoded"
                    # Replace variables in credentials before sending
                    username = self.replace_variables(
                        auth.credentials.body_params.username or ""
                    )
                    password = self.replace_variables(
                        auth.credentials.body_params.password or ""
                    )
                    request_data = {"username": username, "password": password}
                    logger.debug(f"Auth body_params data: {request_data}")
                else:
                    logger.error(
                        "body_params auth selected but no body_params credentials found."
                    )
                    return None
            elif auth_type == "json_body":
                if auth.credentials.json_body:
                    auth_headers["Content-Type"] = "application/json"
                    # Recursively replace variables in the JSON body structure
                    request_json = self._replace_variables_in_dict(
                        auth.credentials.json_body
                    )
                    logger.debug(f"Auth json_body data: {request_json}")
                else:
                    logger.error(
                        "json_body auth selected but no json_body credentials found."
                    )
                    return None
            elif auth_type == "query_params":
                # Assuming username/password are in body_params for query_params type
                if auth.credentials.body_params:
                    username = self.replace_variables(
                        auth.credentials.body_params.username or ""
                    )
                    password = self.replace_variables(
                        auth.credentials.body_params.password or ""
                    )
                    params = {"username": username, "password": password}
                    qstring = "&".join(
                        [f"{quote(k)}={quote(v)}" for k, v in params.items() if v]
                    )
                    auth_url = f"{auth_url}?{qstring}"
                    logger.debug(f"Auth query_params URL: {auth_url}")
                else:
                    logger.error(
                        "query_params auth selected but no body_params credentials found for parameters."
                    )
                    return None
            elif auth_type == "custom_header":
                if auth.credentials.header:
                    # Add all headers from credentials.header, replacing variables in values
                    custom_headers = {
                        k: self.replace_variables(v)
                        for k, v in auth.credentials.header.dict().items()
                        if v is not None
                    }
                    auth_headers.update(custom_headers)
                    logger.debug(f"Auth custom_header headers added: {custom_headers}")
                else:
                    logger.error(
                        "custom_header auth selected but no header credentials found."
                    )
                    return None

            # --- Make the Authentication Request ---
            async with session.request(
                method,
                auth_url,
                headers=auth_headers,
                data=request_data,
                json=request_json,
                timeout=aiohttp.ClientTimeout(
                    total=10
                ),  # Add timeout for auth requests
            ) as resp:
                logger.debug(
                    f"Auth request to {auth_url} returned status {resp.status}"
                )
                if resp.status in [
                    200,
                    201,
                    204,
                ]:  # Consider 204 No Content as potentially successful if no token needed
                    try:
                        # Attempt to get token, handle cases where response might be empty or not JSON
                        if resp.content_length == 0:
                            logger.warning(
                                "Auth successful (status {resp.status}) but response body is empty."
                            )
                            # Decide if an empty response is acceptable for some auth types
                            # For now, assume a token is expected unless it's maybe basic auth?
                            # If basic/bearer auth worked, the 'token' is the header itself, maybe return a success indicator?
                            # Returning a placeholder for now, adjust as needed.
                            return (
                                "authenticated_no_token"
                                if auth_type in ["basic", "bearer"]
                                else None
                            )

                        response_data = await resp.json()
                        # --- Extract Token ---
                        # TODO: Make token extraction configurable?
                        # Common patterns: look for 'token', 'access_token', 'authToken' etc.
                        token = (
                            response_data.get("auth_token")
                            or response_data.get("token")
                            or response_data.get("access_token")
                        )

                        if token:
                            logger.debug(
                                f"Extracted auth token: {token[:10]}..."
                            )  # Log prefix
                            return str(token)  # Ensure it's a string
                        else:
                            logger.warning(
                                f"Auth successful (status {resp.status}) but no token found in response: {response_data}"
                            )
                            # Decide if this is acceptable (e.g., cookie-based session established)
                            return "authenticated_no_token"  # Indicate success without a specific token to carry
                    except aiohttp.ContentTypeError:
                        logger.error(
                            f"Auth failed: Response from {auth_url} was not JSON (status {resp.status})."
                        )
                        return None
                    except json.JSONDecodeError:
                        logger.error(
                            f"Auth failed: Could not decode JSON response from {auth_url} (status {resp.status})."
                        )
                        return None
                else:
                    # Log failure details
                    error_body = await resp.text()
                    logger.warning(
                        f"Auth request failed: Status {resp.status}, URL: {auth_url}, Response: {error_body[:200]}"
                    )
                    return None

        except aiohttp.ClientError as e:
            logger.error(f"Auth request network error to {auth_url}: {e}")
            return None
        except asyncio.TimeoutError:
            logger.error(f"Auth request timed out to {auth_url}")
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error during authentication request to {auth_url}: {e}",
                exc_info=True,
            )
            return None

    async def perform_request(
        self,
        session: aiohttp.ClientSession,
        base_headers: Dict[
            str, str
        ],  # Base headers for this specific request (includes XFF)
        user_web_headers: Dict[str, str],  # Default headers for this user if web
        user_web_ua: str,  # Default UA for this user if web
        user_api_headers: Dict[str, str],  # Default headers for this user if API
        user_api_ua: str,  # Default UA for this user if API
        sim_user: SimulatedUser,  # Current state of the user (auth token etc.)
    ):
        """Performs a single request based on the sitemap and user state."""
        # Determine available paths based on user auth state
        available_path_defs = list(self.site_map.paths)  # Start with non-auth paths
        if sim_user.is_authenticated and self.site_map.paths_auth_req:
            available_path_defs.extend(self.site_map.paths_auth_req)

        if not available_path_defs:
            logger.warning(
                f"User {base_headers.get(self.config.xff_header_name, 'Unknown IP')} has no available paths. Skipping request."
            )
            await asyncio.sleep(0.5)  # Prevent busy-loop if no paths available
            return

        # Choose a path definition and a specific path from it
        path_def = random.choice(available_path_defs)
        method = path_def.method.upper()  # Already validated
        path_template = random.choice(path_def.paths)
        path = self.replace_variables(path_template)  # Replace variables like @id

        # --- Construct URL (handling DNS override) ---
        final_url: str
        request_headers = base_headers.copy()  # Start with XFF header etc.

        if self.config.traffic_target_dns_override:
            port = self.parsed_url.port or self.default_port
            if (self.original_scheme == "https" and port != 443) or (
                self.original_scheme == "http" and port != 80
            ):
                netloc = f"{self.target_ip}:{port}"
            else:
                netloc = self.target_ip  # IP only if default port
            final_url = urlunparse((self.original_scheme, netloc, path, "", "", ""))
            request_headers["Host"] = self.original_host  # Add Host header
        else:
            # Use the original hostname from the config URL
            final_url = urlunparse(
                (self.original_scheme, self.parsed_url.netloc, path, "", "", "")
            )
            # No Host header needed unless it was globally defined

        # --- Determine Headers ---
        # Merge base, global, type-specific, user-specific, and overrides
        if path_def.traffic_type == "web":
            request_headers.update(user_web_headers)  # User's default web headers
            request_headers["User-Agent"] = user_web_ua
        else:  # api
            request_headers.update(user_api_headers)  # User's default API headers
            request_headers["User-Agent"] = user_api_ua

        # Apply bearer token if user is authenticated and has a token
        # Handle the case where auth succeeded but didn't yield a specific token to carry
        if (
            sim_user.is_authenticated
            and sim_user.auth_token
            and sim_user.auth_token != "authenticated_no_token"
        ):
            # TODO: Make auth header name configurable? Assumes Bearer for now.
            request_headers["Authorization"] = f"Bearer {sim_user.auth_token}"

        # Apply path-specific header overrides from sitemap
        if self.site_map.path_headers_override:
            oh = self.site_map.path_headers_override
            # Check if path_headers_override is defined and has paths/headers
            if oh.paths and oh.headers:
                for override_pattern in oh.paths:
                    # Use the matching function to see if the current path matches the pattern
                    if self.match_path(path, override_pattern):
                        logger.debug(
                            f"Applying header override for path {path} matching pattern {override_pattern}"
                        )
                        # Replace variables in override header values
                        override_headers = {
                            k: self.replace_variables(v) for k, v in oh.headers.items()
                        }
                        request_headers.update(override_headers)
                        break  # Apply only the first matching override pattern's headers

        # --- Determine Body ---
        request_data = None
        request_json = None
        if path_def.body:
            # Replace variables in the body template
            body_content = self.replace_variables(path_def.body)
            # Try to parse as JSON if API type and Content-Type suggests JSON
            content_type = request_headers.get("Content-Type", "").lower()
            is_json_content = "application/json" in content_type

            if path_def.traffic_type == "api" and is_json_content:
                try:
                    request_json = json.loads(body_content)
                    logger.debug(f"Request body (JSON): {request_json}")
                except json.JSONDecodeError:
                    logger.warning(
                        f"Body provided for API request to {path} with JSON Content-Type, but failed to parse as JSON. Sending as raw data. Body: {body_content[:100]}..."
                    )
                    request_data = (
                        body_content  # Send as raw data if JSON parsing fails
                    )
                    # Ensure Content-Type doesn't mislead server if we send raw data
                    # request_headers['Content-Type'] = 'text/plain' # Or remove? Or keep original? Keep for now.

            else:
                # For web requests or non-JSON API requests, send as form data or raw data
                # Assuming www-form-urlencoded if content-type suggests it, else raw
                if "application/x-www-form-urlencoded" in content_type:
                    # Attempt to parse as key=value pairs if needed, or just send raw?
                    # For simplicity, sending raw for now. Parse if required by target.
                    request_data = body_content
                    logger.debug(
                        f"Request body (form-data/raw): {request_data[:100]}..."
                    )
                else:
                    request_data = body_content  # Default to raw data
                    logger.debug(f"Request body (raw): {request_data[:100]}...")

        # --- Execute Request ---
        try:
            logger.debug(f"Request: {method} {final_url} Headers: {request_headers}")
            async with session.request(
                method,
                final_url,
                headers=request_headers,
                data=request_data,
                json=request_json,
                timeout=aiohttp.ClientTimeout(total=15),  # Timeout for regular requests
            ) as resp:
                # Consume the response body fully to free up the connection
                await resp.read()
                # Always log the response status when debug logging is enabled
                logger.debug(f"Response {resp.status} for {method} {final_url}")
                # Log errors/warnings based on status code
                if resp.status >= 500:
                    logger.error(f"Server Error {resp.status} for {method} {final_url}")
                elif resp.status >= 400:
                    logger.warning(
                        f"Client Error {resp.status} for {method} {final_url}"
                    )
                else:
                    logger.debug(f"Success {resp.status} for {method} {final_url}")

        # Handle potential exceptions during the request
        except aiohttp.ClientError as e:
            logger.error(f"Network error during request to {final_url}: {e}")
        except asyncio.TimeoutError:
            logger.error(f"Request timed out to {final_url}")
        except Exception as e:
            # Catch any other unexpected errors during request execution
            logger.error(
                f"Unexpected error during request to {final_url}: {e}", exc_info=True
            )

    def match_path(self, request_path: str, pattern: str) -> bool:
        """
        Matches a request path against a pattern.
        Handles simple wildcards like '@variable' segments.
        Example: /users/@id matches /users/123 but not /users/123/profile
        """
        # Normalize paths by removing leading/trailing slashes and splitting
        request_parts = request_path.strip("/").split("/")
        pattern_parts = pattern.strip("/").split("/")

        # If the number of segments doesn't match, they can't be equal
        if len(request_parts) != len(pattern_parts):
            return False

        # Compare segments one by one
        for req_part, pat_part in zip(request_parts, pattern_parts):
            if pat_part.startswith("@"):
                # If the pattern part is a variable placeholder (@...), it matches any segment
                continue
            if req_part != pat_part:
                # If any non-variable segment doesn't match, the paths don't match
                return False

        # If all segments matched (considering variables), the path matches the pattern
        return True

    def replace_variables(self, text: str) -> str:
        """Replaces @variable placeholders in text with values from sitemap.variables."""
        if not self.site_map.variables or "@" not in text:
            return text  # No variables defined or no placeholders found

        modified_text = text
        # Find all placeholders like @varname
        placeholders = re.findall(r"@([a-zA-Z0-9_]+)", modified_text)

        for var_name in placeholders:
            placeholder = f"@{var_name}"
            if var_name in self.site_map.variables:
                var_def = self.site_map.variables[var_name]
                value = None
                try:
                    if var_def.type == "range":
                        # Ensure value is a list of two integers
                        if (
                            isinstance(var_def.value, list)
                            and len(var_def.value) == 2
                            and all(isinstance(x, int) for x in var_def.value)
                        ):
                            value = random.randint(var_def.value[0], var_def.value[1])
                        else:
                            logger.error(
                                f"Invalid 'range' definition for variable @{var_name}: Expected list of two integers, got {var_def.value}"
                            )
                            continue  # Skip replacing this invalid variable
                    elif var_def.type == "list":
                        # Ensure value is a non-empty list
                        if isinstance(var_def.value, list) and var_def.value:
                            value = random.choice(var_def.value)
                        else:
                            logger.error(
                                f"Invalid 'list' definition for variable @{var_name}: Expected non-empty list, got {var_def.value}"
                            )
                            continue  # Skip replacing this invalid variable
                    else:
                        # Should be caught by Pydantic validation, but belt-and-suspenders
                        logger.error(
                            f"Unsupported variable type '{var_def.type}' for @{var_name}"
                        )
                        continue

                    # Convert value to string for replacement
                    val_str = str(value)

                    # URL-encode the value if it contains characters that need encoding in a URL path segment
                    # We check if the placeholder is likely part of a path/query vs body
                    # Simple check: if it's not the only thing in the string, assume URL context?
                    # A more robust solution might need context awareness.
                    # For now, encode if it contains unsafe characters typically encoded in paths/queries.
                    if re.search(r"[^a-zA-Z0-9._~-]", val_str):
                        # Check if already percent-encoded
                        if not re.match(r"%[0-9a-fA-F]{2}", val_str):
                            encoded_val_str = quote(
                                val_str, safe=""
                            )  # Encode everything except null
                            logger.debug(
                                f"Variable @{var_name} value '{val_str}' URL-encoded to '{encoded_val_str}'"
                            )
                            val_str = encoded_val_str

                    # Replace all occurrences of the placeholder
                    modified_text = modified_text.replace(placeholder, val_str)

                except Exception as e:
                    logger.error(f"Error processing variable @{var_name}: {e}")
            else:
                logger.warning(
                    f"Placeholder {placeholder} found in template, but variable '@{var_name}' is not defined in sitemap.variables."
                )

        return modified_text

    def _replace_variables_in_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively replaces variables in dictionary values."""
        new_dict = {}
        for key, value in data.items():
            if isinstance(value, dict):
                new_dict[key] = self._replace_variables_in_dict(value)
            elif isinstance(value, list):
                new_dict[key] = self._replace_variables_in_list(value)
            elif isinstance(value, str):
                new_dict[key] = self.replace_variables(value)
            else:
                new_dict[key] = (
                    value  # Keep non-string, non-dict, non-list values as is
                )
        return new_dict

    def _replace_variables_in_list(self, data: List[Any]) -> List[Any]:
        """Recursively replaces variables in list elements."""
        new_list = []
        for item in data:
            if isinstance(item, dict):
                new_list.append(self._replace_variables_in_dict(item))
            elif isinstance(item, list):
                new_list.append(self._replace_variables_in_list(item))
            elif isinstance(item, str):
                new_list.append(self.replace_variables(item))
            else:
                new_list.append(item)  # Keep other types as is
        return new_list

    def generate_random_ip(self) -> str:
        """Generates a random public IPv4 address."""
        while True:
            # Generate octets ensuring the IP is likely public
            first_octet = random.randint(1, 223)
            # Avoid common private ranges early
            if first_octet == 10 or first_octet == 127:
                continue
            if first_octet == 172:
                second_octet = random.randint(0, 255)
                if 16 <= second_octet <= 31:
                    continue  # Skip 172.16.0.0/12
            elif first_octet == 192:
                second_octet = random.randint(0, 255)
                if second_octet == 168:
                    continue  # Skip 192.168.0.0/16
            elif first_octet == 100:
                second_octet = random.randint(0, 255)
                if 64 <= second_octet <= 127:
                    continue  # Skip 100.64.0.0/10 (Shared Address Space)
            elif first_octet == 169:
                second_octet = random.randint(0, 255)
                if second_octet == 254:
                    continue  # Skip 169.254.0.0/16 (Link-local)
            else:
                second_octet = random.randint(0, 255)

            third_octet = random.randint(0, 255)
            fourth_octet = random.randint(1, 254)  # Avoid .0 and .255 for host part

            # Construct and return the IP
            return f"{first_octet}.{second_octet}.{third_octet}.{fourth_octet}"
