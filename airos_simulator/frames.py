"""CDP and LLDP: the announcements a real antenna sends by itself (nobody asks for
them), as the frames udapi-bridge sends."""
from __future__ import annotations

import struct


CDP_MAC = bytes.fromhex("01000ccccccc")
LLDP_MAC = bytes.fromhex("0180c200000e")
LLDP_TRUE = ("on", "enabled", "1", "enable", "active", "true")   # as udapi-bridge reads it

def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    return ~(total + (total >> 16)) & 0xFFFF


def cdp_frame(src: bytes, device_id: str, addrs: list[str], port: str, software: str,
              platform: str, ttl: int = 180) -> bytes:
    """A CDPv2 announcement: 802.3 + LLC/SNAP to 01:00:0c:cc:cc:cc."""
    def tlv(kind: int, value: bytes) -> bytes:
        return struct.pack("!HH", kind, 4 + len(value)) + value
    addresses = struct.pack("!I", len(addrs)) + b"".join(
        bytes([1, 1, 0xCC]) + struct.pack("!H", 4) + bytes(int(x) for x in ip.split("."))
        for ip in addrs)
    body = (tlv(0x0001, device_id.encode()) + tlv(0x0002, addresses) + tlv(0x0003, port.encode())
            + tlv(0x0004, struct.pack("!I", 0x08)) + tlv(0x0005, software.encode())
            + tlv(0x0006, platform.encode()))
    cdp = bytes([2, ttl]) + b"\x00\x00" + body
    cdp = cdp[:2] + struct.pack("!H", _checksum(cdp)) + cdp[4:]
    payload = b"\xaa\xaa\x03\x00\x00\x0c\x20\x00" + cdp
    return CDP_MAC + src + struct.pack("!H", len(payload)) + payload


def lldp_frame(src: bytes, chassis: bytes, port: str, hostname: str, description: str,
               addrs: list[str], ttl: int = 240) -> bytes:
    """An LLDPDU (IEEE 802.1AB) to 01:80:c2:00:00:0e: chassis by MAC, port by name, TTL,
    system name and description and the management addresses."""
    def tlv(kind: int, value: bytes) -> bytes:
        return struct.pack("!H", (kind << 9) | len(value)) + value
    body = (tlv(1, b"\x04" + chassis) + tlv(2, b"\x05" + port.encode())
            + tlv(3, struct.pack("!H", ttl)) + tlv(5, hostname.encode())
            + tlv(6, description.encode()))
    for ip in addrs:
        body += tlv(8, bytes([5, 1]) + bytes(int(x) for x in ip.split(".")) + bytes([2])
                    + struct.pack("!I", 0) + bytes([0]))
    return LLDP_MAC + src + struct.pack("!H", 0x88CC) + body + tlv(0, b"")
