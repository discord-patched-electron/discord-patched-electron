"""Reviewed Canary 1.0.2510 patch chain; see docs/agent/upstream-update-review.md#reviewed-client-snapshots."""

STAGES = {
    "hrd": (
        "5cfe1cace7c971f53505393c64a77b86edbbc3cd5536e7a4441e626799e94c5d",
        "d6812d26af0e80c62e451adf550c7f05abdaebd08ae6d2d8898951567fed9b01",
        (
            (
                0x14FB000,
                bytes.fromhex("8bb88c0300008d347fc1ee02"),
                bytes.fromhex("8bb08c0300008d3c76c1ef02"),
            ),
            (
                0x14FDD1A,
                bytes.fromhex("8bbdec0300008d347fc1ee02"),
                bytes.fromhex("8bb5ec0300008d3c76c1ef02"),
            ),
        ),
    ),
    "dimensions": (
        "d6812d26af0e80c62e451adf550c7f05abdaebd08ae6d2d8898951567fed9b01",
        "72c88fa101487bad3c8c9417f6a81fc027d398ebd940e993b53c1badfcace237",
        (
            (
                0x15901FE,
                bytes.fromhex("83c02083e0c083c12083e1c0"),
                bytes.fromhex("83c00183e0fe83c10183e1fe"),
            ),
        ),
    ),
    "decoder": (
        "72c88fa101487bad3c8c9417f6a81fc027d398ebd940e993b53c1badfcace237",
        "ae9565cd13f85d24e2ad1fd81988c02067bd60bbddf8ec15cd4b2779c6593dd2",
        (
            (0x45F782, bytes.fromhex("7850"), bytes.fromhex("7842")),
            (
                0x45F7A9,
                bytes.fromhex(
                    "89c5e8a0f6f1ff84c00f85ca000000488d3d900b3f01488d358edb3f01488d0de5dd3f01ba7b120000eb2989c5e875f6f1ff84c00f859f000000488d3d650b3f01488d3563db3f01488d0dd6dd3f01bab31200004189e831c0e809512500eb79"
                ),
                bytes.fromhex(
                    "89c5e8a0f6f1ff84c00f85ca000000488d0df3dd3f01ba7b120000eb2983f8f57509448d780ce9bc00000089c5e875f6f1ff84c00f859f000000488d0de4dd3f01bab3120000488d3d590b3f01488d3557db3f014189e831c0e809512500eb79"
                ),
            ),
        ),
    ),
    "chroma": (
        "ae9565cd13f85d24e2ad1fd81988c02067bd60bbddf8ec15cd4b2779c6593dd2",
        "a76b64a894f1b0fb807002294f953808b6e2e85a18debb99e31f5f2e1c56b73f",
        ((0x197A644, bytes.fromhex(""), bytes.fromhex("")),),
    ),
    "clock": (
        "a76b64a894f1b0fb807002294f953808b6e2e85a18debb99e31f5f2e1c56b73f",
        "15fac535194bbb08448fb4e97d695eb863d02b4fb37e55d5f472a4b40f1fdf32",
        ((0x460030, bytes.fromhex("418b46104889442428"), bytes.fromhex("498b46204889442428")),),
    ),
    "pacing": (
        "15fac535194bbb08448fb4e97d695eb863d02b4fb37e55d5f472a4b40f1fdf32",
        "b8cf1766aab3cf1ba1e084effb454a386d26dd623a94f24201268a833ef12706",
        (
            (
                0x18C2484,
                bytes.fromhex("312e302c323837352c38302c34302c2d36302c3300"),
                bytes.fromhex("322e352c303035302c38302c34302c2d36302c3300"),
            ),
        ),
    ),
    "pulse-reconnect": (
        "b8cf1766aab3cf1ba1e084effb454a386d26dd623a94f24201268a833ef12706",
        "5ad4980d44711716b37e5da190924d609e8d226a64170b7e3f8b16cf0e64516a",
        (
            (
                0x477DF0,
                bytes.fromhex(
                    "534883bf700200000074025bc34889fbe8cbe3efff84c07521488d3d43853d01488d357a8f3e01488d0d26943e01ba610f000031c0e8e6ca2300e8a1a1e1ff488983700200004889c7e8b288e1ff488d3522943e014889c7e8a382e1ff48898378020000488d3505f6ffff4889c74889dae8ba89e1ff488bbb78020000488d35fce8ffff4889dae87489e1ff488bbb7802000031f6ba0100000031c9e8efade1ff488bbb70020000488d35d8933e01e88c96e1ff488bbb700200005be9afa3e1ff"
                ),
                bytes.fromhex(
                    "53488d9f70020000488b3b4885ff751fe8cba1e1ff4889034889c7488d3565943e01e81997e1ff488b3be841a4e1ff488b3be8f98ce1ff488b7b084885ff741ee8bbace1ff3c05726e488dbb90fdffffe86b010000488b7b08e8b2b0e1ff488b3be89a88e1ff488d350a943e014889c7e88b82e1ff48894308488d35f0f5ffff4889c7488d9390fdffffe8a189e1ff488b7b08488d35e6e8ffff488d9390fdffffe85a89e1ff488b7b0831f631d2ffc231c9e8d9ade1ff488b3be88183e1ff5bc3"
                ),
            ),
            (
                0x1B511B9,
                bytes.fromhex("410e1083024b0e08410e1002af0e08"),
                bytes.fromhex("410e10830202bf0e08c30000000000"),
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
