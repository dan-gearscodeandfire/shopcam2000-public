"""DirectShow VideoProcAmp / CameraControl access for the USB OBSBOTs — runs
on the Blue Iris host. Unlike opening the capture pin, property access via
IAMVideoProcAmp does NOT need the streaming pin, so it can work while Blue
Iris is live on the device (verify with `report` first).

    venv\Scripts\python procamp.py --device "Tiny 4K" report
    venv\Scripts\python procamp.py --device "Tiny 4K" set --prop WhiteBalance --value 4600
    venv\Scripts\python procamp.py --device "Tiny 4K" set --prop WhiteBalance --auto

Output is JSON. Property values are the device's native units (WhiteBalance =
Kelvin on UVC cams).
"""
from __future__ import annotations

import argparse
import json
import sys
from ctypes import POINTER, c_long

from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from pygrabber.dshow_graph import SystemDeviceEnum
from pygrabber.dshow_ids import DeviceCategories


class IAMVideoProcAmp(IUnknown):
    _iid_ = GUID("{C6E13360-30AC-11D0-A18C-00A0C9118956}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetRange",
                  (["in"], c_long, "prop"),
                  (["out"], POINTER(c_long), "pMin"),
                  (["out"], POINTER(c_long), "pMax"),
                  (["out"], POINTER(c_long), "pStep"),
                  (["out"], POINTER(c_long), "pDefault"),
                  (["out"], POINTER(c_long), "pCaps")),
        COMMETHOD([], HRESULT, "Set",
                  (["in"], c_long, "prop"),
                  (["in"], c_long, "value"),
                  (["in"], c_long, "flags")),
        COMMETHOD([], HRESULT, "Get",
                  (["in"], c_long, "prop"),
                  (["out"], POINTER(c_long), "value"),
                  (["out"], POINTER(c_long), "flags")),
    ]


class IAMCameraControl(IUnknown):
    _iid_ = GUID("{C6E13370-30AC-11D0-A18C-00A0C9118956}")
    _methods_ = IAMVideoProcAmp._methods_


PROCAMP = {"Brightness": 0, "Contrast": 1, "Hue": 2, "Saturation": 3,
           "Sharpness": 4, "Gamma": 5, "ColorEnable": 6, "WhiteBalance": 7,
           "BacklightCompensation": 8, "Gain": 9}
CAMCTRL = {"Pan": 0, "Tilt": 1, "Roll": 2, "Zoom": 3, "Exposure": 4,
           "Iris": 5, "Focus": 6}
FLAG_AUTO, FLAG_MANUAL = 1, 2


def bind(sub: str):
    sde = SystemDeviceEnum()
    names = sde.get_available_filters(DeviceCategories.VideoInputDevice)
    hits = [i for i, n in enumerate(names) if sub.lower() in n.lower()]
    if len(hits) != 1:
        sys.exit(json.dumps({"error": f"device match {sub!r} ambiguous/absent",
                             "devices": names}))
    filt, name = sde.get_filter_by_index(DeviceCategories.VideoInputDevice, hits[0])
    return filt, name


def iface(filt, itf):
    try:
        return filt.QueryInterface(itf)
    except Exception:
        return None


def read_all(filt) -> dict:
    out: dict = {}
    for label, itf, table in (("procamp", IAMVideoProcAmp, PROCAMP),
                              ("camctrl", IAMCameraControl, CAMCTRL)):
        api = iface(filt, itf)
        if api is None:
            out[label] = None
            continue
        vals = {}
        for pname, pid in table.items():
            try:
                lo, hi, step, default, caps = api.GetRange(pid)
                value, flags = api.Get(pid)
                vals[pname] = {"value": value,
                               "mode": "auto" if flags & FLAG_AUTO else "manual",
                               "range": [lo, hi, step], "default": default}
            except Exception:
                pass
        out[label] = vals
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    st = sub.add_parser("set")
    st.add_argument("--prop", required=True)
    st.add_argument("--value", type=int)
    st.add_argument("--auto", action="store_true")
    a = ap.parse_args()

    filt, name = bind(a.device)
    if a.cmd == "report":
        print(json.dumps({"device": name, **read_all(filt)}, indent=1))
        return

    if a.prop in PROCAMP:
        api, pid = iface(filt, IAMVideoProcAmp), PROCAMP[a.prop]
    elif a.prop in CAMCTRL:
        api, pid = iface(filt, IAMCameraControl), CAMCTRL[a.prop]
    else:
        sys.exit(json.dumps({"error": f"unknown prop {a.prop}"}))
    if api is None:
        sys.exit(json.dumps({"error": "interface unavailable"}))

    if a.auto:
        _, _, _, default, _ = api.GetRange(pid)
        api.Set(pid, default, FLAG_AUTO)
    else:
        api.Set(pid, a.value, FLAG_MANUAL)
    value, flags = api.Get(pid)
    print(json.dumps({"device": name, "prop": a.prop, "value": value,
                      "mode": "auto" if flags & FLAG_AUTO else "manual"}))


if __name__ == "__main__":
    main()
