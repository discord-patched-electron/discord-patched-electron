"""Reviewed Canary 1.0.2484 patch chain; see docs/agent/upstream-update-review.md#reviewed-client-snapshots."""

STAGES = {
    "hrd": (
        "6a534e76dd8a3ac5b06a6e074a13b971b1b7840673d11e580ed3139e079aedb2",
        "f1f0f57a00bafff6ba3db1880f2b366404285b1716d6efee68bb31fbabc38bfa",
        (
            (
                0x14F7300,
                bytes.fromhex("8bb88c0300008d347fc1ee02"),
                bytes.fromhex("8bb08c0300008d3c76c1ef02"),
            ),
            (
                0x14FA01A,
                bytes.fromhex("8bbdec0300008d347fc1ee02"),
                bytes.fromhex("8bb5ec0300008d3c76c1ef02"),
            ),
        ),
    ),
    "dimensions": (
        "f1f0f57a00bafff6ba3db1880f2b366404285b1716d6efee68bb31fbabc38bfa",
        "9d92dc9ae7484047e7826588fa4627f056b35ce12375635f9c1da20b83617412",
        (
            (
                0x158C4FE,
                bytes.fromhex("83c02083e0c083c12083e1c0"),
                bytes.fromhex("83c00183e0fe83c10183e1fe"),
            ),
        ),
    ),
    "decoder": (
        "9d92dc9ae7484047e7826588fa4627f056b35ce12375635f9c1da20b83617412",
        "5b0964dc06c74ac430a8c9a1a2af8e6e57a67564eba4b67e0594b946f6a90f8d",
        (
            (0x45BFF2, bytes.fromhex("7850"), bytes.fromhex("7842")),
            (
                0x45C019,
                bytes.fromhex(
                    "89c5e87015f2ff84c00f85ca000000488d3dcb023f01488d35ced13f01488d0d25d43f01ba7b120000eb2989c5e84515f2ff84c00f859f000000488d3da0023f01488d35a3d13f01488d0d16d43f01bab31200004189e831c0e8994b2500eb79"
                ),
                bytes.fromhex(
                    "89c5e87015f2ff84c00f85ca000000488d0d33d43f01ba7b120000eb2983f8f57509448d780ce9bc00000089c5e84515f2ff84c00f859f000000488d0d24d43f01bab3120000488d3d94023f01488d3597d13f014189e831c0e8994b2500eb79"
                ),
            ),
        ),
    ),
    "chroma": (
        "5b0964dc06c74ac430a8c9a1a2af8e6e57a67564eba4b67e0594b946f6a90f8d",
        "79226d4fd018fb6ce0daf4032546a24ddfebd924db7c9f1881f27ec47c22014f",
        ((0x1976444, bytes.fromhex(""), bytes.fromhex("")),),
    ),
    "clock": (
        "79226d4fd018fb6ce0daf4032546a24ddfebd924db7c9f1881f27ec47c22014f",
        "b9605358e132e9126eebc19eabbe678d551f34117e88c0ce3eed823a2f57dae5",
        ((0x45C8A0, bytes.fromhex("418b46104889442428"), bytes.fromhex("498b46204889442428")),),
    ),
    "pacing": (
        "b9605358e132e9126eebc19eabbe678d551f34117e88c0ce3eed823a2f57dae5",
        "ff183bfe321be1e6762f5c28720982bf3c057bcd1f5ef5edba1f87c296401742",
        (
            (
                0x18BE284,
                bytes.fromhex("312e302c323837352c38302c34302c2d36302c3300"),
                bytes.fromhex("322e352c303035302c38302c34302c2d36302c3300"),
            ),
        ),
    ),
    "pulse-reconnect": (
        "ff183bfe321be1e6762f5c28720982bf3c057bcd1f5ef5edba1f87c296401742",
        "b487dfe6d9a524ed7f7a2e15acebe05811e412b55161cfc7075801369c6d0c21",
        (
            (
                0x474640,
                bytes.fromhex(
                    "534883bf700200000074025bc34889fbe8bb02f0ff84c07521488d3d9e7c3d01488d35da853e01488d0d868a3e01ba610f000031c0e896c52300e861d9e1ff488983700200004889c7e862c0e1ff488d35828a3e014889c7e853bae1ff48898378020000488d3505f6ffff4889c74889dae86ac1e1ff488bbb78020000488d35fce8ffff4889dae824c1e1ff488bbb7802000031f6ba0100000031c9e87fe5e1ff488bbb70020000488d35388a3e01e83ccee1ff488bbb700200005be95fdbe1ff"
                ),
                bytes.fromhex(
                    "53488d9f70020000488b3b4885ff751fe88bd9e1ff4889034889c7488d35c58a3e01e8c9cee1ff488b3be8f1dbe1ff488b3be8a9c4e1ff488b7b084885ff741ee84be4e1ff3c05726e488dbb90fdffffe86b010000488b7b08e842e8e1ff488b3be84ac0e1ff488d356a8a3e014889c7e83bbae1ff48894308488d35f0f5ffff4889c7488d9390fdffffe851c1e1ff488b7b08488d35e6e8ffff488d9390fdffffe80ac1e1ff488b7b0831f631d2ffc231c9e869e5e1ff488b3be831bbe1ff5bc3"
                ),
            ),
            (
                0x1B4C4D9,
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
