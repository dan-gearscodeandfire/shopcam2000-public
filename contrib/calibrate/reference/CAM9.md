# CAM9 - Desktop capture of the shop PC screen.

> **This file is the source of truth for CAM9.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** none -- desktop capture source, no imaging control plane
- **Locked:** no
- **Clip ceiling:** 🔴 UNMEASURED - go measure it, never assume 255
- **Settle after a write:** 0.0 s

## Exposed parameters - and how each range is KNOWN

`probed` = written until it refused. `device` = the device reported it.
🔴 **`assumed` = NOT VERIFIED. Treat an assumed range as a task, not a fact** -
this fleet has burned sessions on assumed ranges, and `drivers.capabilities()`
still reports every Dahua knob as 0–100, which is false for Gamma.

_No imaging controls._

## Known-dead or inert knobs

_None known._ Absence of a known-dead knob is not proof one works.

## What is TRUE about this camera - measured, not assumed

- IT HAS NO IMAGE SETTINGS AT ALL. There is nothing to calibrate. It is deliberately absent from drivers.fleet(). Skip it.
- `type` = 4 identifies a network camera; do not use screencap=1 to tell what a camera is.

## Open / unmeasured

_Nothing outstanding._

## Last known good - the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM9` after a deliberate change,
and record why in the history below.

```json
{
 "note": "not in the fleet snapshot"
}
```

## Validation history - APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- n/a - nothing to validate.

