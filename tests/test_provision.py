"""Wifi provisioning, tested against a stand-in for the ESP32.

`FakeEsp32` mirrors handleConfig() in ai-camera-firmware's ws_server.cpp --
the SET+ prefix, the state/errors reply shape, and the restart-sta command --
so these tests exercise the real conversation rather than a mock of my own
assumptions about it.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import WSMsgType, web

from rvr.provision import ProvisionError, set_station_mode, validate


class FakeEsp32:
    def __init__(self, *, connect_ok=True, ip="192.168.1.77"):
        self.connect_ok = connect_ok
        self.ip = ip
        self.received: list[dict] = []

    async def handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # The firmware greets every client with check_info before anything else.
        await ws.send_str(json.dumps(
            {"Name": "GalaxyRVR", "Type": "GalaxyRVR", "Check": "SC", "StaIp": ""}
        ))

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            if msg.data == "ping":
                await ws.send_str("pong 1")
                continue
            if not msg.data.startswith("SET+"):
                continue

            config = json.loads(msg.data[4:])
            self.received.append(config)
            result = {"state": "ERROR", "errors": []}

            if "staSsid" in config:
                if 0 < len(config["staSsid"]) <= 32:
                    result["state"] = "OK"
                else:
                    result["errors"].append("STA_SSID_INVALID")
            if "staPassword" in config:
                if 8 <= len(config["staPassword"]) <= 64:
                    result["state"] = "OK"
                else:
                    result = {"state": "ERROR", "errors": ["STA_PASSWORD_INVALID"]}
            if config.get("command") == "restart-sta":
                if self.connect_ok:
                    result = {"state": "OK", "ip": self.ip}
                else:
                    result = {"state": "ERROR", "errors": ["STA_CONNECT_ERROR"]}

            await ws.send_str(json.dumps(result))
        return ws


@pytest.fixture
async def esp32(aiohttp_server):
    device = FakeEsp32()

    async def start(**kw):
        for key, value in kw.items():
            setattr(device, key, value)
        app = web.Application()
        app.router.add_get("/", device.handler)
        server = await aiohttp_server(app)
        return device, server.host, server.port

    return start


async def test_joins_the_network_and_reports_the_new_address(esp32):
    device, host, port = await esp32()
    ip = await set_station_mode(host, "MiWiFi", "clave-larga-123", port=port)

    assert ip == "192.168.1.77"
    assert device.received[0] == {"staSsid": "MiWiFi", "staPassword": "clave-larga-123"}
    assert device.received[1] == {"command": "restart-sta"}


async def test_wrong_password_explains_the_2ghz_trap(esp32):
    """STA_CONNECT_ERROR is also what a 5GHz-only network produces, and that is
    a genuinely baffling failure if nobody tells you."""
    _device, host, port = await esp32(connect_ok=False)
    with pytest.raises(ProvisionError, match="2.4GHz"):
        await set_station_mode(host, "MiWiFi", "clave-larga-123", port=port)


async def test_unreachable_rover_says_what_to_check(esp32):
    with pytest.raises(ProvisionError, match="GalaxyRVR"):
        await set_station_mode("127.0.0.1", "MiWiFi", "clave-larga-123", port=1, timeout=3)


@pytest.mark.parametrize(
    "ssid, password, match",
    [
        ("", "clave-larga-123", "1-32 characters"),
        ("x" * 33, "clave-larga-123", "1-32 characters"),
        ("MiWiFi", "corta", "8-64 characters"),
        ("MiWiFi", "", "open network"),
    ],
)
def test_invalid_input_is_caught_before_touching_the_rover(ssid, password, match):
    with pytest.raises(ProvisionError, match=match):
        validate(ssid, password)


def test_a_valid_pair_passes():
    validate("MiWiFi", "clave-larga-123")
