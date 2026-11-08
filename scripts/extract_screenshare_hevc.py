"""Extract only the fixed-key synthetic HEVC fixture (RFC 7798, no DONL)."""

import argparse
from pathlib import Path

from extract_screenshare_h264 import extract_recording


def nal_type(nalu):
    if len(nalu) < 3 or nalu[0] & 128 or not nalu[1] & 7:
        raise ValueError("Invalid HEVC NAL header")
    return (nalu[0] >> 1) & 63


def frame_nalus(payloads):
    nalus, fragment = [], None
    for payload in payloads:
        kind = nal_type(payload)
        if kind < 48:
            if fragment is not None:
                raise ValueError("Incomplete HEVC fragment")
            nalus.append(payload)
        elif kind == 48:  # Aggregation packet; fixture negotiates no DONL/DOND.
            if fragment is not None:
                raise ValueError("Incomplete HEVC fragment")
            offset = 2
            while offset < len(payload):
                if offset + 2 > len(payload):
                    raise ValueError("Truncated HEVC aggregation")
                size = int.from_bytes(payload[offset : offset + 2], "big")
                offset += 2
                if size < 3 or offset + size > len(payload):
                    raise ValueError("Invalid HEVC aggregation length")
                nalu = payload[offset : offset + size]
                if nal_type(nalu) >= 48:
                    raise ValueError("Nested HEVC packetization")
                nalus.append(nalu)
                offset += size
        elif kind == 49:  # Fragmentation unit.
            if len(payload) < 4 or payload[2] & 63 >= 48:
                raise ValueError("Invalid HEVC fragment")
            start, end = bool(payload[2] & 128), bool(payload[2] & 64)
            header = bytes([(payload[0] & 0x81) | ((payload[2] & 63) << 1), payload[1]])
            if start:
                if fragment is not None or end:
                    raise ValueError("Unexpected HEVC fragment start")
                fragment = header + payload[3:]
            else:
                if fragment is None or fragment[:2] != header:
                    raise ValueError("Missing or inconsistent HEVC fragment")
                fragment += payload[3:]
            if end:
                nalus.append(fragment)
                fragment = None
        else:
            raise ValueError("Unsupported HEVC packetization")
    if fragment is not None or not nalus:
        raise ValueError("Incomplete HEVC frame")
    return b"".join(b"\x00\x00\x00\x01" + nalu for nalu in nalus)


def extract(directory):
    return extract_recording(directory, "h265-vaapi", frame_nalus, "HEVC", "hevc.265")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(extract(parser.parse_args().directory))
