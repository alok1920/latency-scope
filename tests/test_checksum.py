from latencyscope.core.checksum import internet_checksum


def test_zero_payload():
    # All-zero data -> checksum is the one's complement of 0 = 0xFFFF.
    assert internet_checksum(b"\x00\x00\x00\x00") == 0xFFFF


def test_hand_computed_case():
    # words 0x0001 + 0x0002 = 0x0003 -> ~0x0003 & 0xFFFF = 0xFFFC
    assert internet_checksum(b"\x00\x01\x00\x02") == 0xFFFC


def test_carry_fold():
    # 0xFFFF + 0x0001 = 0x10000 -> fold -> 0x0001 -> ~ -> 0xFFFE
    assert internet_checksum(b"\xff\xff\x00\x01") == 0xFFFE


def test_odd_length_is_padded():
    # Must not raise on odd-length input.
    c = internet_checksum(b"\x01\x02\x03")
    assert 0 <= c <= 0xFFFF


def test_checksum_verifies_to_zero():
    # Appending the checksum and re-summing yields 0 (valid-packet property).
    # Uses an even-length buffer so word alignment is preserved.
    data = b"hello-icmp-payload"  # 18 bytes (even)
    assert len(data) % 2 == 0
    c = internet_checksum(data)
    verify = internet_checksum(data + c.to_bytes(2, "big"))
    assert verify == 0
