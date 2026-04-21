"""Shared test helpers — importable from any working directory via relative import."""

from __future__ import annotations

import re
import uuid

from httpx import AsyncClient


async def admin_login(client: AsyncClient, admin_key: str = "dev-admin-key") -> None:
    """Log in to the admin dashboard via cookie-based auth.

    POSTs to /admin/login with the given admin key, which sets
    an ``admin_session`` cookie on the client for subsequent requests.

    Because the server sets the cookie with ``Secure=True`` and tests use
    ``http://test`` as the base URL, httpx would refuse to send the cookie
    on subsequent requests. We work around this by extracting the token from
    the ``set-cookie`` header and injecting it directly into ``client.cookies``.
    """
    resp = await client.post("/admin/login", data={"admin_key": admin_key}, follow_redirects=False)
    assert resp.status_code in (200, 303), (
        f"Admin login failed with status {resp.status_code}: {resp.text}"
    )
    # Extract the session token from the set-cookie header and set it manually
    # to work around Secure cookie restrictions in the test transport.
    set_cookie = resp.headers.get("set-cookie", "")
    if "admin_session=" in set_cookie:
        token = set_cookie.split("admin_session=")[1].split(";")[0]
        client.cookies.set("admin_session", token)


async def ack_with_csrf(
    client: AsyncClient,
    ack_token: str,
    *,
    acked_by: str = "Tester",
    note: str = "",
) -> httpx.Response:  # noqa: F821  (forward ref for type hint only)
    """Submit the ACK form with proper CSRF token handling.

    1. GET the ACK page to obtain the CSRF cookie.
    2. Extract the CSRF hidden field value from the HTML.
    3. Manually set the CSRF cookie (server sets Secure=True but tests use http://).
    4. POST with both the cookie and the form field.
    """
    get_resp = await client.get(f"/a/{ack_token}")
    assert get_resp.status_code == 200, get_resp.text

    # Extract CSRF token from the hidden form field
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
    csrf_value = match.group(1) if match else ""

    # Manually set the csrf_token cookie because the server sets it with
    # Secure=True but tests use http:// transport.
    set_cookie = get_resp.headers.get("set-cookie", "")
    if "csrf_token=" in set_cookie:
        token = set_cookie.split("csrf_token=")[1].split(";")[0]
        client.cookies.set("csrf_token", token)

    data: dict[str, str] = {"acked_by": acked_by}
    if note:
        data["note"] = note
    if csrf_value:
        data["csrf_token"] = csrf_value

    return await client.post(f"/a/{ack_token}", data=data)


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self.jobs: list[tuple[str, tuple]] = []

    async def close(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    async def incr(self, key: str) -> int:
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    async def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        return True

    async def enqueue_job(self, name: str, *args, **kwargs) -> None:  # noqa: ARG002
        self.jobs.append((name, args))


async def trigger_alarm(client: AsyncClient) -> uuid.UUID:
    """Trigger an alarm via the Yealink endpoint and return the alarm UUID."""
    response = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["alarm_id"])
