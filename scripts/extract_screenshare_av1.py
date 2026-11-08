"""Extract only the private screenshare fixture's fixed-key AV1 RTP into IVF.

Accepts packets.bin emitted by --record-packets. Never reads a Discord account,
real call key or network socket. Intended for independent synthetic decoder checks.
"""

import argparse
import ctypes
import ctypes.util
import json
import struct
from pathlib import Path


def leb128(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_leb128(data, offset):
    result = 0
    for shift in range(0, 56, 7):
        if offset >= len(data):
            raise ValueError("Truncated AV1 element length")
        value = data[offset]
        offset += 1
        result |= (value & 127) << shift
        if not value & 128:
            return result, offset
    raise ValueError("Oversized AV1 element length")


def restore_obu(element):
    if not element or element[0] & 0x81:
        raise ValueError("Invalid AV1 OBU header")
    header_size = 2 if element[0] & 4 else 1
    if len(element) < header_size:
        raise ValueError("Truncated AV1 OBU extension")
    if element[0] & 2:
        return element
    return (
        bytes([element[0] | 2])
        + element[1:header_size]
        + leb128(len(element) - header_size)
        + element[header_size:]
    )


def frame_obus(payloads):
    complete, fragment = [], b""
    for payload in payloads:
        if not payload:
            raise ValueError("Empty AV1 RTP payload")
        aggregation = payload[0]
        count = (aggregation >> 4) & 3
        offset, elements = 1, []
        while offset < len(payload):
            if count and len(elements) == count - 1:
                length = len(payload) - offset
            else:
                length, offset = read_leb128(payload, offset)
            if not length or offset + length > len(payload):
                raise ValueError("Invalid AV1 RTP element length")
            elements.append(payload[offset : offset + length])
            offset += length
        if count and len(elements) != count:
            raise ValueError("AV1 RTP element count mismatch")
        for index, element in enumerate(elements):
            continuation = index == 0 and bool(aggregation & 0x80)
            if continuation != bool(fragment):
                raise ValueError("Missing or unexpected AV1 fragment")
            element = fragment + element
            fragment = b""
            if index == len(elements) - 1 and aggregation & 0x40:
                fragment = element
            else:
                complete.append(restore_obu(element))
    if fragment:
        raise ValueError("Incomplete AV1 frame")
    return b"".join(complete)


def decryptor():
    library = ctypes.util.find_library("sodium")
    if not library:
        raise RuntimeError("The synthetic packet check needs system libsodium")
    sodium = ctypes.CDLL(library)
    sodium.sodium_init()
    decrypt = sodium.crypto_aead_xchacha20poly1305_ietf_decrypt
    decrypt.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulonglong,
        ctypes.c_void_p,
        ctypes.c_ulonglong,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    decrypt.restype = ctypes.c_int

    def packet_payload(packet):
        if len(packet) < 32 or packet[0] >> 6 != 2:
            raise ValueError("Invalid RTP packet")
        header = 12 + 4 * (packet[0] & 15)
        extension = 0
        if packet[0] & 16:
            if len(packet) < header + 4:
                raise ValueError("Truncated RTP extension")
            extension = struct.unpack_from("!H", packet, header + 2)[0] * 4
            header += 4
        plaintext = ctypes.create_string_buffer(len(packet))
        length = ctypes.c_ulonglong()
        nonce = packet[-4:] + bytes(20)
        result = decrypt(
            plaintext,
            ctypes.byref(length),
            None,
            packet[header:-4],
            len(packet) - header - 4,
            packet[:header],
            header,
            nonce,
            bytes([1] * 32),
        )
        if result:
            raise ValueError("Packet does not authenticate with the private fixture key")
        data = plaintext.raw[: length.value]
        if extension > len(data):
            raise ValueError("Invalid decrypted RTP extension length")
        data = data[extension:]
        if packet[0] & 32:
            padding = data[-1] if data else 0
            if not padding or padding > len(data):
                raise ValueError("Invalid RTP padding")
            data = data[:-padding]
        return data

    return packet_payload


def extract(directory):
    config = json.loads((directory / "config.json").read_text())
    if config.get("encoder") != "av1-vaapi" or config.get("record_packets") is not True:
        raise ValueError("Input is not an explicitly recorded synthetic AV1 fixture")
    data = (directory / "packets.bin").read_bytes()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Synthetic packet recording exceeds its bound")
    decrypt = decryptor()
    offset, groups, seen, authenticated = 0, {}, set(), 0
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("Truncated packet recording")
        size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if not 32 <= size <= 65535 or offset + size > len(data):
            raise ValueError("Invalid recorded packet size")
        packet = data[offset : offset + size]
        offset += size
        if packet[1] & 127 != 101 or struct.unpack_from("!I", packet, 8)[0] != 44:
            continue
        sequence, timestamp = struct.unpack_from("!HI", packet, 2)
        if sequence in seen:
            continue
        seen.add(sequence)
        payload = decrypt(packet)
        authenticated += 1
        groups.setdefault(timestamp, []).append((sequence, payload, bool(packet[1] & 128)))
    frames, skipped = [], 0
    for timestamp, packets in groups.items():
        first = packets[0][0]
        packets.sort(key=lambda packet: (packet[0] - first) & 0xFFFF)
        if not packets[-1][2] or any(
            b[0] != (a[0] + 1) & 0xFFFF for a, b in zip(packets, packets[1:], strict=False)
        ):
            skipped += 1
            continue
        frames.append((timestamp, frame_obus([packet[1] for packet in packets])))
    if not frames:
        raise ValueError("No complete authenticated AV1 frames")
    target = directory / "synthetic-native-av1.ivf"
    with target.open("wb") as output:
        output.write(
            struct.pack(
                "<4sHH4sHHIIII",
                b"DKIF",
                0,
                32,
                b"AV01",
                config["width"],
                config["height"],
                90000,
                1,
                len(frames),
                0,
            )
        )
        for timestamp, frame in frames:
            output.write(struct.pack("<IQ", len(frame), (timestamp - frames[0][0]) & 0xFFFFFFFF))
            output.write(frame)
    report = {
        "authenticatedPackets": authenticated,
        "completeFrames": len(frames),
        "skippedFrames": skipped,
    }
    (directory / "packet-extraction.json").write_text(json.dumps(report, indent=2) + "\n")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(extract(parser.parse_args().directory))
