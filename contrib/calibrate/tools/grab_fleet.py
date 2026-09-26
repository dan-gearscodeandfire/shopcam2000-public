"""Grab a full-res snapshot from several cameras in one login, back-to-back,
so they capture as close to the same instant as the BI API allows.

    python tools/grab_fleet.py OUTDIR CAM1 CAM2 CAM4 CAM5 CAM7
"""
from __future__ import annotations
import asyncio, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from shopcam2000 import config as cfg          # noqa: E402
from shopcam2000.blueiris import BlueIrisClient # noqa: E402


async def main() -> None:
    outdir = pathlib.Path(sys.argv[1]); outdir.mkdir(parents=True, exist_ok=True)
    cams = sys.argv[2:]
    c = cfg.load()
    bi = BlueIrisClient(c.blue_iris.host, c.blue_iris.port,
                        c.blue_iris.user, c.blue_iris.password)
    try:
        await bi.login()
        for cam in cams:
            data = await bi.snapshot(cam, scale_pct=100)
            out = outdir / f"{cam.lower()}.jpg"
            out.write_bytes(data)
            print(f"{cam} -> {out} ({len(data):,} bytes)")
    finally:
        await bi.close()

if __name__ == "__main__":
    asyncio.run(main())
