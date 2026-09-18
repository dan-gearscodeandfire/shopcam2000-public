"""HTTP surface: the page, the state stream, and the four things you can do.

Endpoints are kept deliberately small and stable. The physical ESP32 button is
expected to talk to ``/api/record`` eventually instead of driving Blue Iris
directly, so these are a contract, not an implementation detail.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from .blueiris import BlueIrisClient
from .config import ROOT, STATE_DIR, Config
from .controller import Controller
from .hooks_runner import HookRunner
from .state import ArmingLocked, ControllerState, Settings

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
TEMPLATES = Path(__file__).parent / "templates"

# SSE keepalive. Without periodic traffic, proxies and phone browsers will quietly
# drop an idle stream and the page stops updating with no visible error.
SSE_KEEPALIVE = 15.0


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="Shopcam 2000 Controller", docs_url=None, redoc_url=None)

    state = ControllerState(
        state_dir=STATE_DIR,
        defaults=Settings(
            theme=config.ui.theme,
            hide_unavailable=config.ui.hide_unavailable,
            audio_matters=config.ui.audio_matters,
            thumbnail_interval=config.ui.thumbnail_interval,
            stop_hold_ms=config.ui.stop_hold_ms,
            ui_scale=config.ui.ui_scale,
            keep_awake=config.ui.keep_awake,
        ),
        default_armed=config.recording.default_armed,
    )
    client = BlueIrisClient(
        host=config.blue_iris.host,
        port=config.blue_iris.port,
        user=config.blue_iris.user,
        password=config.blue_iris.password,
        timeout=config.blue_iris.timeout,
    )
    hooks = HookRunner(ROOT / "hooks.py")
    controller = Controller(config, state, client, hooks)

    app.state.controller = controller

    class RevalidatedStatic(StaticFiles):
        """Static files that must be checked against the server before reuse.

        StaticFiles sends ETag and Last-Modified but no Cache-Control, and a
        response with neither Cache-Control nor Expires is left to the browser's
        heuristic: reuse for a fraction of the file's age without asking. That is
        reasonable for a CDN and wrong here, where a deploy is an scp and the
        next page load should be the new one - measured 2026-07-28, a browser
        served a minutes-old app.js from cache and the fix looked like it had
        not deployed at all.

        `no-cache` still caches; it just forces revalidation, so an unchanged
        file costs a 304 rather than a re-download.
        """

        def file_response(self, *args, **kwargs):
            response = super().file_response(*args, **kwargs)
            response.headers["Cache-Control"] = "no-cache"
            return response

    app.mount("/static", RevalidatedStatic(directory=STATIC), name="static")

    # Firmware distribution for the TWAB button (firmware/twab/README.md).
    # Publishing an update = dropping twab/version.txt + twab/twab.bin here;
    # the button compares version.txt against its own build on every
    # heartbeat and updates itself.
    firmware_dir = STATE_DIR / "firmware"
    firmware_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/firmware", StaticFiles(directory=firmware_dir), name="firmware")

    @app.on_event("startup")
    async def _startup() -> None:
        await controller.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await controller.stop()

    # ------------------------------------------------------------------ page

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        # 🕐 The one GET that resets the idle clock, and the reasoning is worth
        # keeping: OPENING the page is an event, having it open is not. Every
        # other read the page performs — the SSE stream, /api/state, thumbnails,
        # static files — arrives on a timer from a tab that may have been left up
        # on a bench overnight, and counting those would hold the clock open for
        # ever. A page load is a person arriving. See Controller.note_activity.
        controller.note_activity("opening the page")
        return HTMLResponse((TEMPLATES / "index.html").read_text(encoding="utf-8"))

    @app.get("/manifest.webmanifest")
    async def manifest() -> FileResponse:
        return FileResponse(STATIC / "manifest.webmanifest",
                            media_type="application/manifest+json")

    @app.get("/sw.js")
    async def service_worker() -> FileResponse:
        # Must be served from the root scope, not /static, or it cannot control
        # the top-level page.
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    # ----------------------------------------------------------------- state

    @app.get("/api/state")
    async def get_state() -> JSONResponse:
        return JSONResponse(controller.snapshot)

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        """Server-sent state, one poller shared by every client."""
        queue = controller.subscribe()

        async def stream():
            try:
                yield f"data: {json.dumps(controller.snapshot)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        snapshot = await asyncio.wait_for(queue.get(), SSE_KEEPALIVE)
                        yield f"data: {json.dumps(snapshot)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                controller.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # --------------------------------------------------------------- actions

    @app.post("/api/arm")
    async def arm(payload: dict) -> JSONResponse:
        camera = payload.get("camera")
        if not camera:
            raise HTTPException(400, "camera is required")
        try:
            await controller.set_armed(camera, bool(payload.get("armed")))
        except ArmingLocked as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse({"ok": True, "armed": controller.snapshot["armed"]})

    @app.post("/api/arm/bulk")
    async def arm_bulk(payload: dict) -> JSONResponse:
        cameras = payload.get("cameras")
        if not isinstance(cameras, list):
            raise HTTPException(400, "cameras must be a list")
        try:
            await controller.set_armed_bulk([str(c) for c in cameras])
        except ArmingLocked as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse({"ok": True, "armed": controller.snapshot["armed"]})

    @app.post("/api/overwatch")
    async def overwatch(payload: dict) -> JSONResponse:
        camera = payload.get("camera")
        if not camera:
            raise HTTPException(400, "camera is required")
        enable = bool(payload.get("overwatch"))
        # A name Blue Iris doesn't know would poison every TWAB press (the set
        # is persisted, and the UI only renders toggles for real cameras, so
        # there'd be no way to click it back off). Removal is always allowed
        # so a stale entry can be cleaned out after a camera is renamed.
        known = {c["name"] for c in controller.snapshot["cameras"]}
        if enable and camera not in known:
            raise HTTPException(404, f"unknown camera {camera!r}")
        await controller.set_overwatch(camera, enable)
        return JSONResponse({"ok": True,
                             "overwatch": controller.snapshot["overwatch"]})

    @app.post("/api/bridges")
    async def bridges(payload: dict) -> JSONResponse:
        """Turn a camera's ffmpeg encoder on or off - or all four at once.

            {"bridge": "cam1", "on": true}    one bridge
            {"all": true}                     every bridge

        This writes what is WANTED. supervisor.ps1 reads that file and does the
        starting and stopping, within a couple of seconds. The Controller never
        kills an encoder itself: the supervisor would restart it inside 30 s and
        log a fault while doing so, which is exactly the job it was given.

        Turning a bridge off takes that camera OFFLINE in Blue Iris. It cannot
        record and a TWAB press will not save it - the receipt says so by name.
        """
        if not controller.bridges.available:
            raise HTTPException(409, "bridge switching is not configured")
        if "on" not in payload:
            raise HTTPException(400, "on is required")
        every = bool(payload.get("all"))
        bridge_id = None if every else payload.get("bridge")
        if bridge_id is None and not every:
            raise HTTPException(400, "bridge or all is required")
        try:
            await controller.set_bridge(bridge_id, bool(payload["on"]))
        except KeyError as exc:
            raise HTTPException(404, f"unknown bridge {bridge_id!r}") from exc
        return JSONResponse({"ok": True, "bridges": controller.snapshot["bridges"]})

    @app.post("/api/twab")
    async def twab(payload: dict | None = None) -> JSONResponse:
        """That Was Awesome - flush the overwatch cameras' pre-roll to disk.

        Called by the physical TWAB button (see firmware/twab), whose POST
        body is a telemetry blob worth keeping in the log; the UI may call it
        with no body at all. Every press is logged - the log is the monitor.

        Status contract (the button retries only what retrying can fix):
        409 = no overwatch cameras selected (config state; retrying is
        pointless, so the firmware treats any 4xx as final). 502 = cameras
        selected but *nothing* triggered (upstream failure; a retry is safe
        because no camera saved). 200 = at least one camera triggered -
        including partial failure, because a retry would re-trigger the
        cameras that already saved; the body's ``failures`` tells the truth.
        """
        log.info("TWAB press: %s", json.dumps(payload or {}, sort_keys=True))
        # "ui" from the page, "espnow" from the button via the bridge. Carried so
        # a page can tell its own press apart from someone else's and skip
        # re-drawing a receipt it already drew.
        body = payload or {}
        source = body.get("source") or body.get("dev") or "unknown"
        result = await controller.twab(source=str(source))
        if result.get("error"):
            raise HTTPException(409, result["error"])
        status = 200 if result["triggered"] else 502
        return JSONResponse(result, status_code=status)

    @app.post("/api/twab/telemetry")
    async def twab_telemetry(payload: dict | None = None) -> JSONResponse:
        """Heartbeat from the TWAB button: log-only, the log is the monitor."""
        log.info("TWAB heartbeat: %s", json.dumps(payload or {}, sort_keys=True))
        return JSONResponse({"ok": True})

    @app.post("/api/record")
    async def record(payload: dict) -> JSONResponse:
        action = (payload.get("action") or "").lower()
        if action == "start":
            result = await controller.start_recording()
        elif action == "stop":
            result = await controller.stop_recording()
        elif action == "toggle":
            result = (await controller.stop_recording()
                      if controller.snapshot["take"]["active"]
                      else await controller.start_recording())
        else:
            raise HTTPException(400, "action must be start, stop or toggle")
        status = 200 if result.get("ok") else 502
        return JSONResponse(result, status_code=status)

    @app.post("/api/settings")
    async def settings(payload: dict) -> JSONResponse:
        await controller.update_settings(payload or {})
        return JSONResponse({"ok": True, "settings": controller.snapshot["settings"]})

    @app.post("/api/hooks/reload")
    async def reload_hooks() -> JSONResponse:
        hooks.load()
        return JSONResponse({"ok": True, "loaded": hooks.loaded,
                             "functions": hooks.available})

    # ----------------------------------------------------------------- media

    @app.get("/api/thumb/{camera}")
    async def thumb(camera: str, refresh: int = 0) -> Response:
        entry = None if refresh else controller.thumbnail(camera)
        if entry is None:
            data = await controller.refresh_thumbnail(camera)
            if data is None:
                raise HTTPException(503, "no image available")
        else:
            data = entry[1]
        return Response(
            data,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/clip/{filename}")
    async def clip(filename: str, request: Request) -> Response:
        """Stream a recorded clip, preserving Range so the browser can seek.

        Proxied rather than linked directly for two reasons: the Blue Iris
        session key never reaches the page, and the clip keeps working if the
        Controller is moved to a different host from Blue Iris.
        """
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(400, "bad filename")

        if client.session is None:
            with contextlib.suppress(Exception):
                await client.login()

        url = client.clip_url(filename)
        headers = {}
        if "range" in request.headers:
            headers["Range"] = request.headers["range"]

        upstream = httpx.AsyncClient(timeout=None)
        try:
            req = upstream.build_request(
                "GET", url, params={"session": client.session}, headers=headers
            )
            response = await upstream.send(req, stream=True)
        except httpx.HTTPError as exc:
            await upstream.aclose()
            raise HTTPException(502, f"could not fetch clip: {exc}") from exc

        if response.status_code >= 400:
            await response.aclose()
            await upstream.aclose()
            raise HTTPException(response.status_code, "clip unavailable")

        passthrough = {
            key: value for key, value in response.headers.items()
            if key.lower() in {"content-type", "content-length", "content-range",
                               "accept-ranges", "last-modified"}
        }

        async def body():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await upstream.aclose()

        return StreamingResponse(body(), status_code=response.status_code,
                                 headers=passthrough)

    return app
