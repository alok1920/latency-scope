"""Internet checksum (RFC 1071) used for ICMP packets."""

from __future__ import annotations


def internet_checksum(data: bytes) -> int:
    """Compute the 16-bit one's-complement checksum of ``data``."""
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    # Fold 32-bit sum into 16 bits.
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF
