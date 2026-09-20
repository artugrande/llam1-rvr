# Handoff — continuing this work in a local session

This project was built in a cloud session with **no access to the rover**. Everything
below the transport layer was verified against a simulator; nothing here has ever
exchanged a byte with real hardware. A local session on the owner's machine can
reach the USB cable and the rover's wifi, which is why the work moves there.

## Where things stand

- The control app runs, 92 tests pass, `./run.sh` works from a clean clone.
- **Never run against the real rover.** Nothing in the hardware path is confirmed.
- **Never run against a real model API.** The agent loop is verified against a
  scripted client only — message framing, tool-result pairing and image trimming
  are known good; whether Claude *navigates well* is completely unknown.

## The immediate blocker

The owner cannot see the rover's wifi network from macOS.

Already ruled out, with evidence:

| Hypothesis | Ruled out because |
|---|---|
| Rover is broken | Chassis RGB shows green = `STATE_IDLE`, i.e. booted fine and waiting |
| Serial switch left in Upload | ESP32 status LED blinks **slow**, which only happens once `start()` has run — and `start()` is only reachable via the `GET+START` command the Arduino sends over the serial link. So the link works and the switch is in Run. |
| Dead batteries | Both of the above would fail first |
| Firmware not flashed | It worked the previous day |

Most likely remaining cause: **the network name**. `wifiConnectAp()` in
`ai-camera-firmware/src/wifi_helper.cpp` appends the last 6 hex digits of the
ESP32's MAC, so the visible SSID is `GalaxyRVR-XXXXXX`, not `GalaxyRVR`. The owner
was told this and still reports not seeing it, so the next step is the cable.

## The cable path (what a local session can do and this one could not)

The ESP32 takes configuration over its serial link, so wifi can be set without ever
joining its access point. Commands are `SET+` or `GET+` followed immediately by the
command name and its value — **no separator, no quotes** — terminated with a newline.

| Command | Effect | Replies |
|---|---|---|
| `GET+RESET` | Firmware version | the version string |
| `SET+STASSID<name>` | Store the network to join | `[OK]` |
| `SET+STAPSK<password>` | Store its password (8–64 chars) | `[OK]` |
| `SET+RSTSTA` | Join it now | `[OK] <ip>` or `[ERROR] STA connect failed` |
| `SET+APSSID<name>` | Change the access point name | `[OK]` |
| `SET+APPSK<password>` | Change its password | `[OK]` |
| `GET+START` | Start the websocket and camera servers | `[OK] <ip>` |
| `SET+RSTCFG` | Factory-reset stored settings | `[OK]` |

Caveat read from the source: `SET+STAPSK` only stores when the password has not
already been changed, *unless* the device has finished booting (`inited == true`).
If a store silently no-ops, `SET+RSTCFG` first.

**The wiring problem to solve first:** the Arduino UNO and the ESP32-CAM share one
UART, and the shield's switch routes it either USB↔Arduino (Upload) or
Arduino↔ESP32 (Run). So the Mac cannot reach the ESP32 directly through the
Arduino's USB port in either position. Two ways around it, unverified:

1. Upload a passthrough sketch to the Arduino that relays USB serial to the ESP32,
   then flip to Run. Whether this works depends on whether the sketch can drive
   both sides — check `DataSerial` / `DebugSerial` in `SunFounder_AI_Camera.h`
   to see if the ESP32 link is a SoftwareSerial (it would then be feasible) or
   the hardware UART (it would not).
2. Simpler: add the two `SET+STASSID` / `SET+STAPSK` calls to `setup()` in
   `galaxy-rvr.ino` via the library's `set()` method, upload once, done. This
   needs no new wiring at all and is probably the right first attempt.

## Hardware facts established by reading firmware (not docs — the docs are wrong)

Sources: `sunfounder/galaxy-rvr` v2.0.0, `sunfounder/ai-camera-firmware`,
`sunfounder/SunFounder_AI_Camera`.

- **Websocket port is 30102**, from `#define PORT "30102"` in `galaxy-rvr.h`. The
  comment beside it and the published docs both say 8765. They are stale.
- **Camera on port 9000**, `/mjpg` (stream) and `/capture` (single frame).
- **Protocol is binary**, not the JSON widget regions older kits use. Frame is
  `0xA0 │ len │ xor │ payload │ 0xA1`.
- **The checksum is asymmetric.** The Arduino's parser validates `XOR(payload)`;
  its emitter computes `XOR(0xA0, len, 0x00, payload…)`. Outbound frames must use
  the first; inbound telemetry must tolerate the second.
- **Out-of-range ultrasonic arrives as 65526** — the firmware returns `-1.0`,
  multiplies by 10 and stuffs it into a `uint16`. Read naively that is 65 metres
  of clear road.
- **AP SSID carries a MAC suffix**: `GalaxyRVR-XXXXXX`, password `12345678`.
- **The Arduino creates the AP, not the ESP32.** `aiCam.begin()` sends
  `SET+SSID` / `SET+PSK` / `GET+START` over serial. With the switch in Upload,
  no network appears at all.
- **LED semantics.** Chassis RGB: orange = initialising, green = idle and waiting,
  magenta = client connected, red = error. ESP32 status LED (pin 33): slow blink =
  no client, solid = client connected, fast blink = error or still starting.
- **Only one servo**, camera tilt, 0–140 with 90 level. No pan.
- **No wheel encoders, no compass.** All navigation must be visual.
- **ESP32 is 2.4GHz only.** A 5GHz-only network fails looking like a bad password.

All of the above is pinned by tests in `tests/test_protocol.py`, which build frames
from a transcription of the firmware's own emitter.

## Suggested order of work

1. Get the rover on the owner's LAN, by cable if the AP stays invisible.
2. `rover_host: <ip>` in `config.yaml`, then `./run.sh --real`.
3. Confirm the camera stream and telemetry before trusting anything else. Watch
   `distance_cm` change as you approach a wall — that validates framing end to end.
4. Drive manually. Confirm the reflex layer refuses forward motion near an obstacle
   and still allows reverse and turning.
5. Only then try a mission, and expect the system prompt and tool descriptions in
   `rvr/tools.py` and `rvr/agent.py` to need tuning against real behaviour.

## Things to be careful about

- The reflex layer sees nothing below bumper height, behind, or at table-edge
  height. **Do not run this near stairs.**
- The control UI has no authentication and binds to `127.0.0.1` for that reason.
- An API key was pasted into the cloud conversation and should be rotated.
