"""ICMP / ICMPv6 echo packet building and parsing.

The packet (de)serialisation here is pure and unit-tested. Actually sending
these over a raw socket (which needs elevated privileges) lives in
``probes.py``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .checksum import internet_checksum

ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0
ICMP6_ECHO_REQUEST = 128
ICMP6_ECHO_REPLY = 129

_HEADER = struct.Struct("!BBHHH")  # type, code, checksum, id, seq


def build_echo_request(identifier: int, sequence: int, payload: bytes = b"",
                       *, ipv6: bool = False) -> bytes:
    """Build an ICMP(v6) echo-request packet.

    For IPv6 the kernel fills in the checksum, so we send 0; for IPv4 we
    compute it ourselves.
    """
    req_type = ICMP6_ECHO_REQUEST if ipv6 else ICMP_ECHO_REQUEST
    identifier &= 0xFFFF
    sequence &= 0xFFFF

    header = _HEADER.pack(req_type, 0, 0, identifier, sequence)
    chksum = 0 if ipv6 else internet_checksum(header + payload)
    header = _HEADER.pack(req_type, 0, chksum, identifier, sequence)
    return header + payload


@dataclass
class EchoReply:
    type: int
    code: int
    identifier: int
    sequence: int
    payload: bytes


def parse_echo_reply(data: bytes, *, ipv6: bool = False,
                     has_ip_header: bool = True) -> EchoReply | None:
    """Parse an ICMP(v6) echo reply.

    IPv4 raw sockets hand back the full IP header; ``has_ip_header`` strips it
    using the IHL field. IPv6 raw sockets deliver the ICMPv6 payload directly.
    Returns ``None`` if the bytes are not a well-formed echo reply.
    """
    buf = data
    if has_ip_header and not ipv6:
        if len(buf) < 20:
            return None
        ihl = (buf[0] & 0x0F) * 4
        if ihl < 20 or len(buf) < ihl + _HEADER.size:
            return None
        buf = buf[ihl:]

    if len(buf) < _HEADER.size:
        return None

    rtype, code, _chksum, ident, seq = _HEADER.unpack(buf[: _HEADER.size])
    expected = ICMP6_ECHO_REPLY if ipv6 else ICMP_ECHO_REPLY
    if rtype != expected:
        return None
    return EchoReply(
        type=rtype,
        code=code,
        identifier=ident,
        sequence=seq,
        payload=buf[_HEADER.size:],
    )
