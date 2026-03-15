# lutron-mcp

MCP server for controlling Lutron Connect Bridge lighting systems over the local network.

Exposes your Lutron lights as MCP tools so any MCP-compatible agent (Claude Code, open-claw, etc.) can control them directly.

## Tools

| Tool | Description |
|------|-------------|
| `list_rooms` | List all rooms in the system |
| `list_lights` | List all lights with zone IDs, names, rooms, and types |
| `get_light_status` | Get current brightness level of a light |
| `set_light_level` | Set brightness (0-100) |
| `turn_light_on` | Turn a light fully on |
| `turn_light_off` | Turn a light off |

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Pair with your bridge

Run the pairing script and press the small black button on the back of your Lutron Connect Bridge when prompted:

```bash
python3 pair.py <bridge_ip>
```

This generates TLS client certificates in `./certs/` that authenticate with your bridge.

### 3. Configure your MCP client

Add to your Claude Code config (`~/.claude.json`) or any MCP client:

```json
{
  "mcpServers": {
    "lutron": {
      "command": "python3",
      "args": ["/path/to/lutron-mcp/mcp_server.py"],
      "env": {
        "LUTRON_BRIDGE_IP": "10.0.0.168"
      }
    }
  }
}
```

### 4. Test with MCP Inspector

```bash
npx @modelcontextprotocol/inspector python3 mcp_server.py
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LUTRON_BRIDGE_IP` | `10.0.0.168` | Bridge IP address |
| `LUTRON_LEAP_PORT` | `8090` | LEAP API port |
| `LUTRON_CERTS_DIR` | `./certs` | Path to certificate directory |

## How it works

The server communicates with the Lutron Connect Bridge using Lutron's LEAP protocol over a persistent TLS connection on port 8090. The pairing process uses Lutron's Connect Local Access Protocol to obtain signed client certificates from the bridge.

## Compatibility

Tested with:
- Lutron Connect Bridge (CONNECT-BDG2)
- LEAP protocol version 1.111
