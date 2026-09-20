"""Putting the rover's wifi into station mode, so your machine keeps its internet.

This is the single biggest practical obstacle to running anything autonomous on
this hardware. Out of the box the ESP32 serves its own access point: you join
`GalaxyRVR` to reach the robot, and in doing so you leave the internet, which
means no model API, which means no voice and no missions. Manual driving is all
that is left.

The fix is to have the ESP32 join your existing network instead, so the rover
and your machine sit on the same LAN and the internet stays up. The firmware
supports this over the control websocket -- `ws_server.cpp` accepts a text frame
beginning `SET+` carrying JSON settings, then a `restart-sta` command that makes
it connect and reports the address it got. That is what this module drives, so
you do not have to hunt for a settings page or reflash anything.
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp

from . import protocol as p

log = logging.getLogger(__name__)

# Bounds enforced by handleConfig() in ws_server.cpp. Checking them here turns a
# silent STA_SSID_INVALID into a sentence you can act on.
SSID_MAX = 32
PASSWORD_MIN, PASSWORD_MAX = 8, 64


class ProvisionError(RuntimeError):
    pass


def validate(ssid: str, password: str) -> None:
    if not 0 < len(ssid) <= SSID_MAX:
        raise ProvisionError(
            f"The network name must be 1-{SSID_MAX} characters; got {len(ssid)}."
        )
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise ProvisionError(
            f"The wifi password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters; "
            f"got {len(password)}. The firmware rejects shorter ones, so an open "
            f"network will not work here."
        )


async def set_station_mode(
    host: str, ssid: str, password: str, *, port: int = p.WS_PORT, timeout: float = 45.0
) -> str:
    """Point the rover at ``ssid`` and return the address it ends up on.

    Run this while your machine is joined to the rover's own access point --
    that is the only way to reach it before it has joined anything else.
    """
    validate(ssid, password)
    url = f"ws://{host}:{port}"
    log.info("connecting to %s", url)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, timeout=aiohttp.ClientWSTimeout(ws_close=15)) as ws:
                handshake = await _next_json(ws, timeout=10)
                if handshake:
                    log.info("rover says: %s", handshake)

                await ws.send_str("SET+" + json.dumps({"staSsid": ssid, "staPassword": password}))
                stored = await _expect_ok(ws, "storing the network details", timeout)

                await ws.send_str("SET+" + json.dumps({"command": "restart-sta"}))
                # Joining a network takes a few seconds, and the firmware only
                # answers once it has finished trying.
                result = await _expect_ok(ws, "joining the network", timeout)

                ip = result.get("ip") or stored.get("ip")
                if not ip:
                    raise ProvisionError(
                        "The rover reported success but gave no address. Check its "
                        "serial output -- it prints the address at boot."
                    )
                return str(ip)
    except aiohttp.ClientError as exc:
        raise ProvisionError(
            f"Could not reach the rover at {url} ({exc}). Are you joined to its "
            f"own wifi network? It is named GalaxyRVR-XXXXXX, with six hex digits "
            f"from the ESP32's MAC appended -- a bare 'GalaxyRVR' will not appear."
        ) from exc


async def _next_json(ws, *, timeout: float) -> dict:
    """Read frames until one parses as a JSON object, or time out."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return {}
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        except asyncio.TimeoutError:
            return {}
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue  # telemetry and pongs are not replies to us
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data


async def _expect_ok(ws, step: str, timeout: float) -> dict:
    """Wait for a config reply and turn an ERROR into a readable message."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ProvisionError(f"The rover never answered while {step}.")
        reply = await _next_json(ws, timeout=remaining)
        if not reply:
            raise ProvisionError(f"The rover never answered while {step}.")
        if "state" not in reply:
            continue  # the connect handshake, or something else; keep waiting
        if reply.get("state") == "OK":
            return reply
        raise ProvisionError(
            f"The rover refused while {step}: {', '.join(reply.get('errors') or ['unknown'])}. "
            f"{_explain(reply.get('errors') or [])}"
        )


def _explain(errors: list) -> str:
    hints = {
        "STA_SSID_INVALID": "The network name is empty or too long.",
        "STA_PASSWORD_INVALID": "The password must be 8-64 characters.",
        "STA_CONNECT_ERROR": (
            "It could not join that network. Check the password, and note the "
            "ESP32 only speaks 2.4GHz -- a 5GHz-only network will never work."
        ),
    }
    return " ".join(hints[e] for e in errors if e in hints)
