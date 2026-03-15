#!/usr/bin/env python3
"""
Pair with a Lutron Connect Bridge to obtain TLS client certificates for the LEAP API.

Uses the Connect LAP certificates (shipped with this repo) to authenticate
with the bridge's pairing port, then exchanges a CSR to get a signed client
certificate for ongoing LEAP API access.

Usage:
    python3 pair.py [bridge_ip]

You will need to press the small black button on the back of the bridge
within 3 minutes of running this script.

Certificates are saved to the ./certs/ directory.
"""

import asyncio
import json
import os
import socket
import ssl
import sys

import orjson
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

BRIDGE_IP = sys.argv[1] if len(sys.argv) > 1 else "10.0.0.168"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CERTS_DIR = os.path.join(SCRIPT_DIR, "certs")

CONNECT_LAP_CA = os.path.join(CERTS_DIR, "connect-lap-ca.crt")
CONNECT_LAP_CERT = os.path.join(CERTS_DIR, "connect-lap.crt")
CONNECT_LAP_KEY = os.path.join(CERTS_DIR, "connect-lap.key")

BUTTON_PRESS_TIMEOUT = 180
SOCKET_TIMEOUT = 10


class JsonSocket:
    def __init__(self, reader, writer):
        self._reader = reader
        self._writer = writer

    async def read_json(self, timeout):
        buffer = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        if buffer == b"":
            return None
        return orjson.loads(buffer)

    async def write_json(self, obj):
        buffer = orjson.dumps(obj)
        self._writer.writelines((buffer, b"\r\n"))
        await self._writer.drain()


async def main():
    print(f"Connecting to Lutron bridge at {BRIDGE_IP}:8083 ...")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_verify_locations(CONNECT_LAP_CA)
    ctx.load_cert_chain(CONNECT_LAP_CERT, CONNECT_LAP_KEY)
    ctx.verify_mode = ssl.CERT_REQUIRED

    print("Generating private key and CSR ...")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "lutron_mcp")])
        )
        .sign(private_key, hashes.SHA256())
    )

    reader, writer = await asyncio.open_connection(
        BRIDGE_IP, 8083, server_hostname="", ssl=ctx, family=socket.AF_INET
    )
    jsock = JsonSocket(reader, writer)

    print()
    print("=" * 60)
    print("  Press the small black button on the back of the bridge!")
    print(f"  You have {BUTTON_PRESS_TIMEOUT // 60} minutes ...")
    print("=" * 60)
    print()

    while True:
        msg = await jsock.read_json(BUTTON_PRESS_TIMEOUT)
        if msg is None:
            print("Connection closed by bridge.")
            return
        header = msg.get("Header", {})
        body = msg.get("Body", {})
        if header.get("ContentType", "").startswith("status;"):
            perms = body.get("Status", {}).get("Permissions", [])
            if "PhysicalAccess" in perms:
                print("Button press detected!")
                break

    print("Requesting signed certificate ...")
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ASCII")
    await jsock.write_json(
        {
            "Header": {
                "RequestType": "Execute",
                "Url": "/pair",
                "ClientTag": "get-cert",
            },
            "Body": {
                "CommandType": "CSR",
                "Parameters": {
                    "CSR": csr_pem,
                    "DisplayName": "lutron_mcp",
                    "DeviceUID": "000000000000",
                    "Role": "Admin",
                },
            },
        }
    )

    while True:
        msg = await jsock.read_json(SOCKET_TIMEOUT)
        if msg is None:
            print("Connection closed unexpectedly.")
            return
        if msg.get("Header", {}).get("ClientTag") == "get-cert":
            break

    signing_result = msg["Body"]["SigningResult"]
    writer.close()

    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    os.makedirs(CERTS_DIR, exist_ok=True)
    with open(os.path.join(CERTS_DIR, "client.key"), "wb") as f:
        f.write(key_pem)
    with open(os.path.join(CERTS_DIR, "client.crt"), "w") as f:
        f.write(signing_result["Certificate"])
    with open(os.path.join(CERTS_DIR, "bridge-ca.crt"), "w") as f:
        f.write(signing_result["RootCertificate"])

    print()
    print(f"Certificates saved to {CERTS_DIR}/")
    print("  - client.key    (your private key)")
    print("  - client.crt    (your signed client certificate)")
    print("  - bridge-ca.crt (bridge CA certificate)")
    print()
    print("You can now run the MCP server:")
    print(f"  LUTRON_BRIDGE_IP={BRIDGE_IP} python3 mcp_server.py")


if __name__ == "__main__":
    asyncio.run(main())
