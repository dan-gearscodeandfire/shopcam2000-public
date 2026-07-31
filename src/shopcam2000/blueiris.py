"""Blue Iris JSON API client.

Verified against Blue Iris 6.0.8.4 and the ``BlueIris.pdf`` manual shipped with
that build (2026-05-22). See ``docs/blue-iris-api.md`` for the findings that
shaped this module, including the ones the manual does not spell out.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class BlueIrisError(RuntimeError):
    """A command was rejected by Blue Iris, or Blue Iris could not be reached."""


class BlueIrisOffline(BlueIrisError):
    """Blue Iris did not answer at all - process down, host down, or wrong address."""


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("ascii")).hexdigest()


class BlueIrisClient:
    """Thin async client over ``POST /json``.

    Blue Iris sessions expire quickly and without warning, so every command is
    prepared to re-authenticate once and retry. Callers should not have to think
    about sessions at all.
    """

    def __init__(self, host: str, port: int, user: str, password: str,
                 timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self._user = user
        self._password = password
        self.base = f"http://{host}:{port}"
        self.url = f"{self.base}/json"
        self._session: str | None = None
        self._version: str | None = None
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=timeout)

    # ----------------------------------------------------------------- session

    @property
    def session(self) -> str | None:
        """The current session key, or None if not authenticated."""
        return self._session

    @property
    def version(self) -> str | None:
        """Blue Iris version as self-reported by the API, e.g. ``6.0.8.4``."""
        return self._version

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await self._client.post(self.url, json=payload)
        except httpx.HTTPError as exc:
            raise BlueIrisOffline(f"cannot reach Blue Iris at {self.url}: {exc}") from exc
        if r.status_code != 200:
            raise BlueIrisError(f"HTTP {r.status_code} from {self.url}")
        try:
            return r.json()
        except ValueError as exc:
            raise BlueIrisError("Blue Iris returned a non-JSON response") from exc

    async def login(self) -> str:
        """Authenticate and store a session key.

        Blue Iris uses a two-step challenge: an unauthenticated ``login`` returns
        ``result: fail`` plus a session nonce, which is then hashed with the
        credentials. The first ``fail`` is expected, not an error.
        """
        first = await self._post({"cmd": "login"})
        nonce = first.get("session")
        if not nonce:
            raise BlueIrisError("Blue Iris did not issue a session nonce")

        response = _md5(f"{self._user}:{nonce}:{self._password}")
        second = await self._post(
            {"cmd": "login", "session": nonce, "response": response}
        )
        if second.get("result") != "success":
            reason = (second.get("data") or {}).get("reason", "unknown")
            raise BlueIrisError(f"Blue Iris login failed: {reason}")

        self._session = nonce
        data = second.get("data") or {}
        self._version = data.get("version")
        if not data.get("clipcreate", True):
            log.warning(
                "Blue Iris user %r lacks the 'clip creation' permission; "
                "starting and stopping manual recording will fail.", self._user
            )
        return nonce

    async def cmd(self, command: str, **params: Any) -> dict[str, Any]:
        """Send a command, authenticating or re-authenticating as required."""
        async with self._lock:
            if self._session is None:
                await self.login()
            payload = {"cmd": command, "session": self._session, **params}
            result = await self._post(payload)

            if result.get("result") != "success":
                # Almost always an expired session. Re-login once and retry;
                # if it fails again the problem is real.
                log.debug("command %r failed, re-authenticating", command)
                self._session = None
                await self.login()
                payload["session"] = self._session
                result = await self._post(payload)

            if result.get("result") != "success":
                reason = (result.get("data") or {}).get("reason") or result.get("data")
                raise BlueIrisError(f"Blue Iris rejected {command!r}: {reason}")
            return result

    # ---------------------------------------------------------------- commands

    async def camlist(self) -> list[dict[str, Any]]:
        """Every camera, with group entries removed.

        Blue Iris returns camera *groups* in the same array as cameras. They are
        identifiable by a leading ``+`` in ``optionDisplay`` (``+All cameras``).
        Arming a group would silently arm its members, so they are dropped here
        rather than filtered in the UI.
        """
        result = await self.cmd("camlist")
        return [
            cam for cam in (result.get("data") or [])
            if not str(cam.get("optionDisplay", "")).startswith("+")
        ]

    async def status(self) -> dict[str, Any]:
        result = await self.cmd("status")
        return result.get("data") or {}

    async def set_manrec(self, camera: str, recording: bool) -> None:
        """Start or stop manual recording on one camera.

        Note the shape: ``manrec`` is a **top-level** parameter of ``camconfig``,
        not nested inside a ``data`` object. Nesting it is accepted and silently
        does nothing.
        """
        await self.cmd("camconfig", camera=camera, manrec=recording)

    async def trigger(self, camera: str, active: bool = True) -> None:
        """Fire - or with ``active=False`` cancel - a camera's trigger.

        Measured behaviour (see ``docs/blue-iris-api.md``): on a camera whose
        Record tab is "when triggered" with a pre-trigger buffer, the trigger
        flushes the buffered pre-roll plus Break-time seconds of post-roll
        into a clip; re-triggering while active extends the same clip rather
        than starting a new one. ``trigger=0`` - a top-level parameter, like
        ``manrec`` - ends an active trigger early. On a camera not configured
        for triggered recording the command is accepted and records nothing.
        """
        if active:
            await self.cmd("trigger", camera=camera)
        else:
            await self.cmd("trigger", camera=camera, trigger=0)

    async def cliplist(self, camera: str | None = None,
                       startdate: int | None = None) -> list[dict[str, Any]]:
        """Clips from the database, optionally limited by camera and start time.

        ``startdate`` is UTC seconds since the epoch.
        """
        params: dict[str, Any] = {}
        if camera:
            params["camera"] = camera
        if startdate is not None:
            params["startdate"] = int(startdate)
        result = await self.cmd("cliplist", **params)
        return result.get("data") or []

    # ------------------------------------------------------------------- media

    async def snapshot(self, camera: str, scale_pct: int = 40,
                       quality: int = 50) -> bytes:
        """A single JPEG frame, scaled down.

        ``scale_pct=40`` cuts a 1080p frame from ~118 KB to ~34 KB, which is
        ample for telling cameras apart and much kinder to a machine that is
        also encoding video.
        """
        if self._session is None:
            async with self._lock:
                if self._session is None:
                    await self.login()
        url = f"{self.base}/image/{camera}"
        params = {"session": self._session, "q": quality, "s": scale_pct}
        try:
            r = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise BlueIrisOffline(f"snapshot for {camera} failed: {exc}") from exc
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            raise BlueIrisError(f"no image for {camera} (HTTP {r.status_code})")
        return r.content

    def clip_url(self, filename: str) -> str:
        """Direct URL for a recorded clip.

        Use ``/clips/<file>`` - it returns ``video/mp4`` and honours Range
        requests, so a browser can seek. Do not use ``/file/clips/<file>``: that
        path returns a *JPEG thumbnail* of the clip with a ``.mp4`` name, which
        looks like a working video URL right up until it isn't.
        """
        return f"{self.base}/clips/{filename}"
