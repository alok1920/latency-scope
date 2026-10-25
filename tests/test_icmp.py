from latencyscope.core import icmp
from latencyscope.core.checksum import internet_checksum


def test_build_echo_request_header():
    pkt = icmp.build_echo_request(0x1234, 7, b"abc")
    assert pkt[0] == icmp.ICMP_ECHO_REQUEST
    assert pkt[1] == 0  # code
    # id and seq are big-endian at offsets 4 and 6
    assert pkt[4:6] == (0x1234).to_bytes(2, "big")
    assert pkt[6:8] == (7).to_bytes(2, "big")
    assert pkt.endswith(b"abc")


def test_build_echo_request_checksum_valid():
    pkt = icmp.build_echo_request(1, 1, b"payload-data")
    # A valid ICMP packet checksums back to zero.
    assert internet_checksum(pkt) == 0


def test_ipv6_checksum_is_zero_field():
    # Kernel computes the ICMPv6 checksum, so the field we emit is zero.
    pkt = icmp.build_echo_request(1, 1, b"x", ipv6=True)
    assert pkt[0] == icmp.ICMP6_ECHO_REQUEST
    assert pkt[2:4] == b"\x00\x00"


def test_roundtrip_parse_without_ip_header():
    pkt = icmp.build_echo_request(0xABCD, 42, b"hi")
    # Simulate a reply by flipping the type to ECHO_REPLY.
    reply_bytes = bytes([icmp.ICMP_ECHO_REPLY]) + pkt[1:]
    reply = icmp.parse_echo_reply(reply_bytes, has_ip_header=False)
    assert reply is not None
    assert reply.identifier == 0xABCD
    assert reply.sequence == 42
    assert reply.payload == b"hi"


def test_parse_strips_ipv4_header():
    pkt = icmp.build_echo_request(5, 9, b"zz")
    reply_icmp = bytes([icmp.ICMP_ECHO_REPLY]) + pkt[1:]
    # Fake 20-byte IPv4 header with IHL=5 (0x45 version+ihl).
    fake_ip = bytes([0x45]) + b"\x00" * 19
    reply = icmp.parse_echo_reply(fake_ip + reply_icmp, has_ip_header=True)
    assert reply is not None
    assert reply.identifier == 5
    assert reply.sequence == 9


def test_parse_rejects_wrong_type():
    pkt = icmp.build_echo_request(1, 1, b"")  # type = ECHO_REQUEST, not reply
    assert icmp.parse_echo_reply(pkt, has_ip_header=False) is None


def test_parse_rejects_truncated():
    assert icmp.parse_echo_reply(b"\x00\x00", has_ip_header=False) is None
