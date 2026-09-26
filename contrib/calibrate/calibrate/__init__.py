"""Fleet colour calibration for Shopcam 2000.

Measurement plane: Blue Iris snapshots (what the edit cuts together).
Control plane: native per camera — Dahua CGI (CAM2/4/7), UVC over SSH on the
BI host (CAM1/CAM5 OBSBOTs). See ../README.md.
"""

# Public copy: the reference files and several docstrings carry emoji and
# em-dashes, and a Windows console is cp1252. Printing one raises
# UnicodeEncodeError AFTER the work is done, so success looks like failure.
# The rig sets PYTHONIOENCODING=utf-8 instead; here, degrade to "?" rather
# than crash, for every tool that imports this package.
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
