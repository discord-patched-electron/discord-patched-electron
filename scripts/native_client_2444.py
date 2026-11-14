"""Reviewed Canary 1.0.2444 native patch sites and complete stage identities.

Patched functions have the same normalized bodies as pinned 1.0.2357;
relative decoder operands were relocated against corresponding original targets.
See docs/agent/upstream-update-review.md#reviewed-client-snapshots for review and runtime evidence.
"""

STAGES = {
    "hrd": (
        "72505e3c8165af5756d789035d2785654df2eb897104c52a444101f8354c53ab",
        "4034f639f5b1f0ac44ab8bc342e2d04844b577752b07df791781310f28e75495",
        (
            (
                0x14C8AD0,
                bytes.fromhex("8bb88c0300008d347fc1ee02"),
                bytes.fromhex("8bb08c0300008d3c76c1ef02"),
            ),
            (
                0x14CB7EA,
                bytes.fromhex("8bbdec0300008d347fc1ee02"),
                bytes.fromhex("8bb5ec0300008d3c76c1ef02"),
            ),
        ),
    ),
    "dimensions": (
        "4034f639f5b1f0ac44ab8bc342e2d04844b577752b07df791781310f28e75495",
        "6aff27481ade24e36d692768e44f093b48ff5b976ddc72e59ffcf40cb64f5c7f",
        (
            (
                0x157A3CE,
                bytes.fromhex("83c02083e0c083c12083e1c0"),
                bytes.fromhex("83c00183e0fe83c10183e1fe"),
            ),
        ),
    ),
    "decoder": (
        "6aff27481ade24e36d692768e44f093b48ff5b976ddc72e59ffcf40cb64f5c7f",
        "ffc7d555e5c968721a139943e4e215d9549c010c018f2fa4e69293aecf136d4d",
        (
            (0x44FF92, bytes.fromhex("7850"), bytes.fromhex("7842")),
            (
                0x44FFB9,
                bytes.fromhex(
                    "89c5e8e095f2ff84c00f85ca000000488d3d9ca23e01488d350e6f3f01488d0d65713f01ba7b120000eb2989c5e8b595f2ff84c00f859f000000488d3d71a23e01488d35e36e3f01488d0d56713f01bab31200004189e831c0e859082500eb79"
                ),
                bytes.fromhex(
                    "89c5e8e095f2ff84c00f85ca000000488d0d73713f01ba7b120000eb2983f8f57509448d780ce9bc00000089c5e8b595f2ff84c00f859f000000488d0d64713f01bab3120000488d3d65a23e01488d35d76e3f014189e831c0e859082500eb79"
                ),
            ),
        ),
    ),
    "chroma": (
        "ffc7d555e5c968721a139943e4e215d9549c010c018f2fa4e69293aecf136d4d",
        "36d17a59791e07c1cf1335fae8007eefa26060c056f41dae93071ccfc1eeaba7",
        # Shader bytes are verified by patch_capture_chroma against the shared
        # SPIR-V asset and its hashes; keep only its relocated offset here.
        ((0x1962724, b"", b""),),
    ),
    "clock": (
        "36d17a59791e07c1cf1335fae8007eefa26060c056f41dae93071ccfc1eeaba7",
        "55d78431b944c80404d1b084f47b84f5f715e8f7a678d2888a53f627999b4b47",
        ((0x450840, bytes.fromhex("418b46104889442428"), bytes.fromhex("498b46204889442428")),),
    ),
    "pacing": (
        "55d78431b944c80404d1b084f47b84f5f715e8f7a678d2888a53f627999b4b47",
        "09c1ddd4eeb5b1583487b1806a21bf57d90bbf5e56be2dca077d8ed8f0b6ae68",
        (
            (
                0x18ABBC4,
                bytes.fromhex("312e302c323837352c38302c34302c2d36302c3300"),
                bytes.fromhex("322e352c303035302c38302c34302c2d36302c3300"),
            ),
        ),
    ),
}
HASHES = {digest for before, after, _ in STAGES.values() for digest in (before, after)}


def reviewed(stage, digest):
    before, after, sites = STAGES[stage]
    if digest in (before, after):
        return before, after, sites
    later = False
    for name, (_, output, _) in STAGES.items():
        later = later or name == stage
        if later and digest == output:
            return digest, digest, sites
    return None
