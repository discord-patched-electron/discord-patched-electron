"""Fixed-key SFU receiver announcements for the isolated screenshare fixture only."""

import base64
import ctypes
import ctypes.util
import struct


def receiver_reports(bitrate, count):
    """Authenticate APP packets using the fixture key, never account credentials.

    Reviewed native App::Parse reads network-order name 0x30534944 (wire 0SID).
    The APP SSRC identifies video stream 44; subtype is the receiver count.
    Its one 12-byte block names synthetic receiver 2000 and its bitrate cap.
    """
    if not 1 <= count <= 10000 or not 1 <= bitrate <= 100000000:
        raise ValueError("Invalid synthetic receiver announcement bounds")
    library = ctypes.util.find_library("sodium")
    if not library:
        raise RuntimeError("The synthetic SFU announcement needs system libsodium")
    sodium = ctypes.CDLL(library)
    sodium.sodium_init()
    encrypt = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt
    encrypt.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.c_void_p,
        ctypes.c_ulonglong,
        ctypes.c_void_p,
        ctypes.c_ulonglong,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    encrypt.restype = ctypes.c_int
    exponent = 0
    while bitrate >= 1 << 18:
        bitrate >>= 1
        exponent += 1
    header = struct.pack("!BBHI", 0x81, 204, 5, 44)
    payload = (
        struct.pack("!IQ", 0x30534944, 2000)
        + ((exponent << 18) | bitrate).to_bytes(3, "big")
        + bytes([0])
    )
    packets = []
    for index in range(count):
        # Disjoint from the bounded fixture peers' low counter ranges.
        suffix = struct.pack("!I", 0xF0000001 + index)
        nonce = suffix + bytes(20)
        output = ctypes.create_string_buffer(len(payload) + 16)
        length = ctypes.c_ulonglong()
        if encrypt(
            output,
            ctypes.byref(length),
            payload,
            len(payload),
            header,
            len(header),
            None,
            nonce,
            bytes([1]) * 32,
        ):
            raise RuntimeError("Could not authenticate synthetic receiver announcement")
        packets.append(base64.b64encode(header + output.raw[: length.value] + suffix).decode())
    return packets
