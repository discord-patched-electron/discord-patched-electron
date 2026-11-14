"""Reviewed Canary 1.0.2483 patch chain; see docs/agent/upstream-update-review.md#reviewed-client-snapshots."""

STAGES = {
    "hrd": (
        "13e302e2f8a20bb83fa9e749356902655ce096136cdaeaaf9389bca718cc411f",
        "d58363b997bad1d16f1afca3afbff812672ec32baa0a0686702a40b29bb5ea3c",
        (
            (
                0x14F5900,
                bytes.fromhex("8bb88c0300008d347fc1ee02"),
                bytes.fromhex("8bb08c0300008d3c76c1ef02"),
            ),
            (
                0x14F861A,
                bytes.fromhex("8bbdec0300008d347fc1ee02"),
                bytes.fromhex("8bb5ec0300008d3c76c1ef02"),
            ),
        ),
    ),
    "dimensions": (
        "d58363b997bad1d16f1afca3afbff812672ec32baa0a0686702a40b29bb5ea3c",
        "94ef9308eea26206d7da35f030c5e37f4b827f31b2e46aed804bfaa19283898d",
        (
            (
                0x158AAFE,
                bytes.fromhex("83c02083e0c083c12083e1c0"),
                bytes.fromhex("83c00183e0fe83c10183e1fe"),
            ),
        ),
    ),
    "decoder": (
        "94ef9308eea26206d7da35f030c5e37f4b827f31b2e46aed804bfaa19283898d",
        "4d735efb611c2e9475a83006c94615575b8257a4f85a3b3be03ada24ced952cd",
        (
            (0x45A662, bytes.fromhex("7850"), bytes.fromhex("7842")),
            (
                0x45A689,
                bytes.fromhex(
                    "89c5e8002ff2ff84c00f85ca000000488d3d5bfc3e01488d355ecb3f01488d0db5cd3f01ba7b120000eb2989c5e8d52ef2ff84c00f859f000000488d3d30fc3e01488d3533cb3f01488d0da6cd3f01bab31200004189e831c0e8a94b2500eb79"
                ),
                bytes.fromhex(
                    "89c5e8002ff2ff84c00f85ca000000488d0dc3cd3f01ba7b120000eb2983f8f57509448d780ce9bc00000089c5e8d52ef2ff84c00f859f000000488d0db4cd3f01bab3120000488d3d24fc3e01488d3527cb3f014189e831c0e8a94b2500eb79"
                ),
            ),
        ),
    ),
    "chroma": (
        "4d735efb611c2e9475a83006c94615575b8257a4f85a3b3be03ada24ced952cd",
        "e498a14029667503fb75d11952188b3c496ef90abab3aedfb34391d47fe853d6",
        ((0x1974444, bytes.fromhex(""), bytes.fromhex("")),),
    ),
    "clock": (
        "e498a14029667503fb75d11952188b3c496ef90abab3aedfb34391d47fe853d6",
        "8e4eb0d082f17733c7fbb1b53a529f95d4026d9e48f1d6657a7adc370d2750d8",
        ((0x45AF10, bytes.fromhex("418b46104889442428"), bytes.fromhex("498b46204889442428")),),
    ),
    "pacing": (
        "8e4eb0d082f17733c7fbb1b53a529f95d4026d9e48f1d6657a7adc370d2750d8",
        "42b32e010c1bbd57bc0eb71b0b751730738d85e282c920f331ffabcb3ef035e5",
        (
            (
                0x18BC284,
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
