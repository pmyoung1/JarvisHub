"""Wake-on-LAN magic packets, pure standard library.

A Samsung TV in standby only answers a magic packet when "Power On with
Mobile" is enabled in its settings, and Ethernet is far more reliable than
Wi-Fi for this (many sets drop the Wi-Fi radio when "off").
"""

from __future__ import annotations

import re
import socket

_MAC_RE = re.compile(r"^[0-9a-fA-F]{12}$")


def parse_mac(mac: str) -> bytes:
    """Accept aa:bb:cc:dd:ee:ff, aa-bb-..., aabb.ccdd.eeff or aabbccddeeff."""
    cleaned = re.sub(r"[\s:.\-]", "", mac or "")
    if not _MAC_RE.match(cleaned):
        raise ValueError(f"invalid MAC address: {mac!r}")
    return bytes.fromhex(cleaned)


def build_magic_packet(mac: str) -> bytes:
    """6 bytes of 0xFF followed by the MAC address repeated 16 times (102 bytes)."""
    raw = parse_mac(mac)
    return b"\xff" * 6 + raw * 16


def send_magic_packet(
    mac: str,
    broadcast: str = "255.255.255.255",
    port: int = 9,
    repeat: int = 3,
    sock_factory=socket.socket,  # noqa: ANN001 - injectable for tests
) -> int:
    """Broadcast the magic packet (UDP) `repeat` times. Returns bytes in one packet."""
    packet = build_magic_packet(mac)
    sock = sock_factory(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(max(1, int(repeat))):
            sock.sendto(packet, (broadcast, int(port)))
    finally:
        sock.close()
    return len(packet)
