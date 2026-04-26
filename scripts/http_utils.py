"""Shared HTTP helpers for local demo scripts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: str
    json_body: dict[str, Any] | list[Any] | None


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def request_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
) -> HttpResult:
    req = request.Request(url=url, data=body, method=method.upper(), headers=headers or {})
    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            payload: dict[str, Any] | list[Any] | None = None
            if raw.strip():
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = None
            return HttpResult(response.status, raw, payload)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        payload: dict[str, Any] | list[Any] | None = None
        if raw.strip():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
        return HttpResult(exc.code, raw, payload)
