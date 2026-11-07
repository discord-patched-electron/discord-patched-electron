"""Extract the fixed-key synthetic H.264 fixture, never real call recordings."""

import argparse
import json
import struct
from pathlib import Path

from extract_screenshare_av1 import decryptor


def frame_nalus(payloads):
    nalus, fragment = [], None
    for payload in payloads:
        if not payload:
            raise ValueError("Empty H.264 payload")
        kind = payload[0] & 31
        if payload[0] & 128:
            raise ValueError("Forbidden H.264 bit")
        if 1 <= kind <= 23:
            if fragment is not None:
                raise ValueError("Incomplete H.264 fragment")
            nalus.append(payload)
        elif kind == 24:  # STAP-A
            if fragment is not None:
                raise ValueError("Incomplete H.264 fragment")
            offset = 1
            while offset < len(payload):
                if offset + 2 > len(payload):
                    raise ValueError("Truncated H.264 aggregation")
                size = int.from_bytes(payload[offset : offset + 2], "big")
                offset += 2
                if not size or offset + size > len(payload):
                    raise ValueError("Invalid H.264 aggregation length")
                nalu = payload[offset : offset + size]
                if nalu[0] & 128 or not 1 <= nalu[0] & 31 <= 23:
                    raise ValueError("Invalid aggregated H.264 NAL")
                nalus.append(nalu)
                offset += size
        elif kind == 28:  # FU-A
            if len(payload) < 3 or payload[1] & 32 or not 1 <= payload[1] & 31 <= 23:
                raise ValueError("Invalid H.264 fragment")
            start, end = bool(payload[1] & 128), bool(payload[1] & 64)
            header = (payload[0] & 0xE0) | (payload[1] & 31)
            if start:
                if fragment is not None or end:
                    raise ValueError("Unexpected H.264 fragment start")
                fragment = bytes([header]) + payload[2:]
            else:
                if fragment is None or fragment[0] != header:
                    raise ValueError("Missing or inconsistent H.264 fragment")
                fragment += payload[2:]
            if end:
                nalus.append(fragment)
                fragment = None
        else:
            raise ValueError("Unsupported H.264 packetization")
    if fragment is not None:
        raise ValueError("Incomplete H.264 frame")
    return b"".join(b"\x00\x00\x00\x01" + nalu for nalu in nalus)


def extract_recording(directory, encoder, decode_frame, label, suffix):
    config = json.loads((directory / "config.json").read_text())
    if config.get("encoder") != encoder or config.get("record_packets") is not True:
        raise ValueError(f"Input is not an explicitly recorded synthetic {label} fixture")
    recording = directory / "packets.bin"
    if recording.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Synthetic packet recording exceeds its bound")
    data = recording.read_bytes()
    decrypt = decryptor()
    offset, groups, seen = 0, {}, set()
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
        seq, timestamp = struct.unpack_from("!HI", packet, 2)
        if seq in seen:
            continue
        seen.add(seq)
        groups.setdefault(timestamp, []).append((seq, decrypt(packet), bool(packet[1] & 128)))
    frames, skipped = [], 0
    for packets in groups.values():
        first = packets[0][0]
        packets.sort(key=lambda packet: (packet[0] - first) & 65535)
        if not packets[-1][2] or any(
            b[0] != (a[0] + 1) & 65535 for a, b in zip(packets, packets[1:], strict=False)
        ):
            skipped += 1
            continue
        frames.append(decode_frame([p[1] for p in packets]))
    if not frames:
        raise ValueError(f"No complete authenticated {label} frames")
    target = directory / f"synthetic-native-{suffix}"
    target.write_bytes(b"".join(frames))
    (directory / "packet-extraction.json").write_text(
        json.dumps({"completeFrames": len(frames), "skippedFrames": skipped}, indent=2) + "\n"
    )
    return target


def extract(directory):
    return extract_recording(directory, "h264-vaapi", frame_nalus, "H.264", "h264.264")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(extract(parser.parse_args().directory))
