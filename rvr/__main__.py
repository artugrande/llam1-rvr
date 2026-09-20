"""Entry point: ``python -m rvr [--mock] [--host 10.0.0.42]``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

from .config import Config
from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rvr", description="Claude-driven Galaxy RVR control")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--host", help="rover address (overrides the config file)")
    parser.add_argument("--bind-host", help="address for the control UI")
    parser.add_argument("--bind-port", type=int, help="port for the control UI")
    parser.add_argument("--model", help="model id for the agent loop")
    parser.add_argument(
        "--base-url",
        help="Anthropic-compatible endpoint, e.g. https://ai-gateway.vercel.sh",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="run against the built-in simulator instead of real hardware",
    )
    parser.add_argument("--log-level", default=None)
    parser.add_argument(
        "--setup-wifi",
        nargs=2,
        metavar=("SSID", "PASSWORD"),
        help="Join the rover to your wifi so your machine keeps its internet. "
        "Run this while connected to the rover's own network (GalaxyRVR).",
    )
    args = parser.parse_args(argv)

    config = Config.load(
        args.config,
        rover_host=args.host,
        bind_host=args.bind_host,
        bind_port=args.bind_port,
        model=args.model,
        base_url=args.base_url,
        mock=args.mock or None,
        log_level=args.log_level,
    )
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    if not config.api_key:
        print(
            "No API key set (ANTHROPIC_API_KEY or AI_GATEWAY_API_KEY). Manual "
            "driving will work; voice commands and autonomy will not.",
            file=sys.stderr,
        )

    # A gateway rejects an unprefixed model id with a confusing 404 on the first
    # mission. Better to say so now than twenty minutes into a drive.
    for problem in config.llm().check_models():
        print(f"WARNING: {problem}", file=sys.stderr)

    if args.setup_wifi:
        return _setup_wifi(config, *args.setup_wifi)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(config))
    return 0


def _setup_wifi(config: Config, ssid: str, password: str) -> int:
    from .provision import ProvisionError, set_station_mode

    print(f"Asking the rover at {config.rover_host} to join {ssid!r}…")
    print("(this takes a few seconds while it connects)")
    try:
        ip = asyncio.run(set_station_mode(config.rover_host, ssid, password))
    except ProvisionError as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 1

    print(f"\nDone. The rover is now on your network at {ip}")
    print("\nNext:")
    print("  1. Rejoin your normal wifi on this machine.")
    print(f"  2. Put `rover_host: {ip}` in config.yaml")
    print("  3. Run: ./run.sh --real")
    print("\nYour machine keeps its internet now, so voice and missions will work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
