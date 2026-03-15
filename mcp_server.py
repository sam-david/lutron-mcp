#!/usr/bin/env python3
"""
Lutron LEAP MCP Server

Exposes Lutron Connect Bridge lighting controls as MCP tools so any
MCP-compatible agent can control lights directly over the local network.

Communicates with the bridge via the LEAP protocol over TLS on port 8090.
Requires pairing certificates in a ./certs/ directory (see pair.py).

Environment variables:
    LUTRON_BRIDGE_IP   - Bridge IP address (default: auto-discovered or 10.0.0.168)
    LUTRON_LEAP_PORT   - LEAP API port (default: 8090)
    LUTRON_CERTS_DIR   - Path to certs directory (default: ./certs relative to this script)
"""

import asyncio
import os
import socket
import ssl
import uuid

import orjson
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

BRIDGE_IP = os.environ.get("LUTRON_BRIDGE_IP", "10.0.0.168")
LEAP_PORT = int(os.environ.get("LUTRON_LEAP_PORT", "8090"))
CERTS_DIR = os.environ.get(
    "LUTRON_CERTS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs"),
)

_reader = None
_writer = None
_lock = asyncio.Lock()
_zone_cache: dict[int, str] = {}
_area_cache: dict[str, str] = {}


def _make_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_verify_locations(os.path.join(CERTS_DIR, "bridge-ca.crt"))
    ctx.load_cert_chain(
        os.path.join(CERTS_DIR, "client.crt"),
        os.path.join(CERTS_DIR, "client.key"),
    )
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


async def _connect():
    global _reader, _writer
    ctx = _make_ssl_context()
    _reader, _writer = await asyncio.open_connection(
        BRIDGE_IP, LEAP_PORT, server_hostname="", ssl=ctx, family=socket.AF_INET
    )
    for _ in range(10):
        try:
            line = await asyncio.wait_for(_reader.readline(), timeout=1)
            data = orjson.loads(line)
            if data.get("CommuniqueType") not in ("SubscribeResponse",):
                break
        except asyncio.TimeoutError:
            break


async def _leap_request(communique_type: str, url: str, body: dict = None) -> dict:
    global _reader, _writer
    async with _lock:
        if _writer is None or _writer.is_closing():
            await _connect()

        tag = str(uuid.uuid4())
        req = {
            "CommuniqueType": communique_type,
            "Header": {"ClientTag": tag, "Url": url},
        }
        if body is not None:
            req["Body"] = body

        _writer.write(orjson.dumps(req) + b"\r\n")
        await _writer.drain()

        for _ in range(20):
            line = await asyncio.wait_for(_reader.readline(), timeout=10)
            data = orjson.loads(line)
            if data.get("Header", {}).get("ClientTag") == tag:
                return data


async def _ensure_caches():
    """Populate zone/area name caches on first use."""
    if _zone_cache:
        return
    area_resp = await _leap_request("ReadRequest", "/area")
    for a in area_resp.get("Body", {}).get("Areas", []):
        _area_cache[a["href"]] = a.get("Name", "Unknown")
    zone_resp = await _leap_request("ReadRequest", "/zone")
    for z in zone_resp.get("Body", {}).get("Zones", []):
        zid = int(z["href"].split("/")[-1])
        _zone_cache[zid] = z.get("Name", f"Zone {zid}")


def _zone_name(zone_id: int) -> str:
    return _zone_cache.get(zone_id, f"Zone {zone_id}")


app = Server("lutron")


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="list_rooms",
            description="List all rooms (areas) in the Lutron lighting system.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="list_lights",
            description=(
                "List all lights (zones) in the Lutron system with their zone IDs, "
                "names, rooms, and control types (Dimmed or Switched). "
                "Use the zone_id from results to control specific lights."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_light_status",
            description="Get the current brightness level of a specific light.",
            inputSchema={
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "integer",
                        "description": "The zone ID of the light (use list_lights to find IDs).",
                    }
                },
                "required": ["zone_id"],
            },
        ),
        Tool(
            name="set_light_level",
            description=(
                "Set a light's brightness to a specific level. "
                "Use 0 for off, 100 for full brightness, or any value in between for dimmers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "integer",
                        "description": "The zone ID of the light to control.",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Brightness level from 0 (off) to 100 (full on).",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
                "required": ["zone_id", "level"],
            },
        ),
        Tool(
            name="turn_light_on",
            description="Turn a light fully on (100% brightness).",
            inputSchema={
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "integer",
                        "description": "The zone ID of the light to turn on.",
                    }
                },
                "required": ["zone_id"],
            },
        ),
        Tool(
            name="turn_light_off",
            description="Turn a light off.",
            inputSchema={
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "integer",
                        "description": "The zone ID of the light to turn off.",
                    }
                },
                "required": ["zone_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        await _ensure_caches()

        if name == "list_rooms":
            resp = await _leap_request("ReadRequest", "/area")
            areas = resp.get("Body", {}).get("Areas", [])
            lines = ["Rooms in the Lutron system:"]
            for a in areas:
                if a.get("Parent"):
                    zones = a.get("AssociatedZones", [])
                    zone_count = len(zones)
                    lines.append(
                        f"  - {a['Name']} ({zone_count} light{'s' if zone_count != 1 else ''})"
                    )
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "list_lights":
            resp = await _leap_request("ReadRequest", "/zone")
            zones = resp.get("Body", {}).get("Zones", [])
            lines = ["Lights in the Lutron system:", ""]
            for z in zones:
                zid = int(z["href"].split("/")[-1])
                area_name = _area_cache.get(
                    z.get("AssociatedArea", {}).get("href", ""), "Unknown"
                )
                lines.append(
                    f"  zone_id={zid}: {z['Name']} ({area_name}) [{z['ControlType']}]"
                )
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "get_light_status":
            zone_id = arguments["zone_id"]
            resp = await _leap_request("ReadRequest", f"/zone/{zone_id}/status")
            status = resp.get("Body", {}).get("ZoneStatus", {})
            level = status.get("Level", "unknown")
            return [TextContent(
                type="text",
                text=f"{_zone_name(zone_id)} is at {level}%.",
            )]

        elif name == "set_light_level":
            zone_id = arguments["zone_id"]
            level = arguments["level"]
            await _leap_request(
                "CreateRequest",
                f"/zone/{zone_id}/commandprocessor",
                {
                    "Command": {
                        "CommandType": "GoToLevel",
                        "Parameter": [{"Type": "Level", "Value": level}],
                    }
                },
            )
            return [TextContent(
                type="text",
                text=f"Set {_zone_name(zone_id)} to {level}%.",
            )]

        elif name == "turn_light_on":
            zone_id = arguments["zone_id"]
            await _leap_request(
                "CreateRequest",
                f"/zone/{zone_id}/commandprocessor",
                {
                    "Command": {
                        "CommandType": "GoToLevel",
                        "Parameter": [{"Type": "Level", "Value": 100}],
                    }
                },
            )
            return [TextContent(
                type="text",
                text=f"Turned on {_zone_name(zone_id)}.",
            )]

        elif name == "turn_light_off":
            zone_id = arguments["zone_id"]
            await _leap_request(
                "CreateRequest",
                f"/zone/{zone_id}/commandprocessor",
                {
                    "Command": {
                        "CommandType": "GoToLevel",
                        "Parameter": [{"Type": "Level", "Value": 0}],
                    }
                },
            )
            return [TextContent(
                type="text",
                text=f"Turned off {_zone_name(zone_id)}.",
            )]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        global _writer
        _writer = None
        return [TextContent(type="text", text=f"Error: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
