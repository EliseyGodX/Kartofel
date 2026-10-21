# ruff: file-ignore[magic-value-comparison]

import json
import re
from collections.abc import Mapping  # ruff: ignore[typing-only-standard-library-import]
from typing import Annotated, Any, Literal, Self
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kartofel.enums import (
    KartofelHTTPMethodEnum,
    KartofelModeEnum,
)

type ModeSettings = (
    SleepModeSettings
    | SteadyModeSettings
    | RampModeSettings
    | SpikeModeSettings
)


DEFAULT_R_ID = -1


class KartofelRequest(BaseModel):
    url: str = Field(min_length=1)
    method: KartofelHTTPMethodEnum
    params: str | None = None
    data: str | None = None
    json_body: dict[str, Any] | None = Field(default=None, alias='json')
    cookies: Mapping[str, str] | None = None
    headers: Mapping[str, str] | None = None
    id: int = DEFAULT_R_ID

    model_config = ConfigDict(populate_by_name=True)

    @field_validator('url')
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            not value.startswith('/')
            or value.startswith('//')
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or '?' in value
            or '#' in value
        ):
            raise ValueError(
                'Expected a relative URL path starting with "/", '
                'without a domain, scheme, query or fragment',
            )
        return value

    @field_validator('params')
    @classmethod
    def validate_params(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith('?'):
            raise ValueError(
                'Query parameters must not start with "?"',
            )
        if '#' in value:
            raise ValueError(
                'Query parameters must not contain a fragment',
            )
        try:
            parse_qsl(
                value,
                keep_blank_values=True,
                encoding='utf-8',
                errors='strict',
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError('Invalid query string') from exc
        return value

    @field_validator('json_body')
    @classmethod
    def validate_json(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is not None:
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError('Invalid JSON body') from exc
        return value

    @field_validator('cookies')
    @classmethod
    def validate_cookies(
        cls,
        value: Mapping[str, str] | None,
    ) -> Mapping[str, str] | None:
        if value is None:
            return None
        for name, cookie_value in value.items():
            if not name:
                raise ValueError('Cookie name must not be empty')
            if any(
                ord(char) < 0x21
                or ord(char) >= 0x7F
                or char in '()<>@,;:\\"/[]?={} \t'
                for char in name
            ):
                raise ValueError(f'Invalid cookie name: {name!r}')
            if any(
                ord(char) < 0x20
                or ord(char) == 0x7F
                or char in ';,'
                for char in cookie_value
            ):
                raise ValueError(
                    f'Invalid cookie value for {name!r}',
                )
        return value

    @field_validator('headers')
    @classmethod
    def validate_headers(
        cls,
        value: Mapping[str, str] | None,
    ) -> Mapping[str, str] | None:
        if value is None:
            return None
        for name, header_value in value.items():
            if not re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+").fullmatch(name):
                raise ValueError(
                    f'Invalid HTTP header name: {name!r}',
                )
            if '\r' in header_value or '\n' in header_value:
                raise ValueError(
                    f'Header {name!r} must not contain CR or LF',
                )
            if any(
                (ord(char) < 0x20 and char != '\t')
                or ord(char) == 0x7F
                for char in header_value
            ):
                raise ValueError(
                    f'Invalid control character in header {name!r}',
                )
        return value

    @model_validator(mode='after')
    def validate_body(self) -> Self:
        if self.data is not None and self.json_body is not None:
            raise ValueError(
                'Specify either data or json, not both',
            )
        return self


class KartofelRequestPack(BaseModel):
    weight: int = Field(gt=0)
    request: KartofelRequest


class KartofelSettings(BaseModel):
    base_url: str
    modes: list[
        Annotated[
            ModeSettings,
            Field(discriminator='mode'),
            ]
        ] = Field(min_length=1)
    connection_limit: int = Field(500, gt=0)
    ttl_dns_cache: int = Field(120, ge=0)
    connection_keepalive_timeout: float = Field(30.0, gt=0)
    request_timeout: float = Field(10.0, gt=0)
    min_request_pack_delay: float = Field(0.15, gt=0)
    print_thread_tick: float = Field(1, gt=0)

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip()
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError('Invalid base URL') from exc
        if (
            parsed.scheme not in {'http', 'https'}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {'', '/'}
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in value)
        ):
            raise ValueError(
                'base_url must be an HTTP(S) origin, '
                'e.g. "https://example.com" or "http://localhost:8080"',
            )
        return value.rstrip('/')


class BaseModeSettings(BaseModel):
    requests: list[KartofelRequestPack] = Field(min_length=1)


class SleepModeSettings(BaseModeSettings):
    mode: Literal[KartofelModeEnum.SLEEP] = KartofelModeEnum.SLEEP
    duration: float = Field(gt=0)
    requests: list[KartofelRequestPack] = Field(
        default_factory=list,
        max_length=0,
    )


class SteadyModeSettings(BaseModeSettings):
    rps: int = Field(gt=0)
    duration: float = Field(gt=0)
    mode: Literal[KartofelModeEnum.STEADY] = KartofelModeEnum.STEADY


class RampModeSettings(BaseModeSettings):
    start_rps: int = Field(gt=0)
    end_rps: int = Field(gt=0)
    rps_step: int = Field(gt=0)
    duration: float = Field(gt=0)
    mode: Literal[KartofelModeEnum.RAMP] = KartofelModeEnum.RAMP

    @model_validator(mode='after')
    def validate_rps(self) -> Self:
        if self.start_rps >= self.end_rps:
            raise ValueError('start_rps must be less than end_rps')
        if self.rps_step > self.end_rps - self.start_rps:
            raise ValueError(
                'rps_step must not exceed end_rps - start_rps',
            )
        if (self.end_rps - self.start_rps) % self.rps_step != 0:
            raise ValueError(
                'end_rps - start_rps must be divisible by rps_step',
            )
        return self


class SpikeModeSettings(BaseModeSettings):
    count: int = Field(gt=0)
    mode: Literal[KartofelModeEnum.SPIKE] = KartofelModeEnum.SPIKE
