"""Reviewed Canary 1.0.2472 patch chain; see docs/agent/upstream-update-review.md#reviewed-client-snapshots."""

STAGES = {
    "hrd": (
        "08faa8dfa9ec3911135a235956198cf3c7462accaaf85b44c58f7bb030df9a8c",
        "90aadf37a8416bb147f373584144be3eacc8b2adca8dd02a14ef24c75d5c6bc4",
        (
            (
                0x14D50D0,
                bytes.fromhex("8bb88c0300008d347fc1ee02"),
                bytes.fromhex("8bb08c0300008d3c76c1ef02"),
            ),
            (
                0x14D7DEA,
                bytes.fromhex("8bbdec0300008d347fc1ee02"),
                bytes.fromhex("8bb5ec0300008d3c76c1ef02"),
            ),
        ),
    ),
    "dimensions": (
        "90aadf37a8416bb147f373584144be3eacc8b2adca8dd02a14ef24c75d5c6bc4",
        "c57b3e20281682c7f6dba714881f03ef8de5cc74e1d04bb197e7a6f78783cbe0",
        (
            (
                0x15869CE,
                bytes.fromhex("83c02083e0c083c12083e1c0"),
                bytes.fromhex("83c00183e0fe83c10183e1fe"),
            ),
        ),
    ),
    "decoder": (
        "c57b3e20281682c7f6dba714881f03ef8de5cc74e1d04bb197e7a6f78783cbe0",
        "165e1eac4a0db94604dccbfb81dc943d26783d545874cf81ccaf012c2f8640f6",
        (
            (0x4590D2, bytes.fromhex("7850"), bytes.fromhex("7842")),
            (
                0x4590F9,
                bytes.fromhex(
                    "89c5e8302af2ff84c00f85ca000000488d3ddbd13e01488d35eea03f01488d0d45a33f01ba7b120000eb2989c5e8052af2ff84c00f859f000000488d3db0d13e01488d35c3a03f01488d0d36a33f01bab31200004189e831c0e8f93c2500eb79"
                ),
                bytes.fromhex(
                    "89c5e8302af2ff84c00f85ca000000488d0d53a33f01ba7b120000eb2983f8f57509448d780ce9bc00000089c5e8052af2ff84c00f859f000000488d0d44a33f01bab3120000488d3da4d13e01488d35b7a03f014189e831c0e8f93c2500eb79"
                ),
            ),
        ),
    ),
    "chroma": (
        "165e1eac4a0db94604dccbfb81dc943d26783d545874cf81ccaf012c2f8640f6",
        "879f41e23bbce1d70b5fa58097365c45d85bb06abf69e7d9d289f6b815bef020",
        ((0x196EBA4, bytes.fromhex(""), bytes.fromhex("")),),
    ),
    "clock": (
        "879f41e23bbce1d70b5fa58097365c45d85bb06abf69e7d9d289f6b815bef020",
        "8b6db14c8500bb8c4876a905797b75b62d0655fd2fb0e6ca049fe3640775f418",
        ((0x459980, bytes.fromhex("418b46104889442428"), bytes.fromhex("498b46204889442428")),),
    ),
    "pacing": (
        "8b6db14c8500bb8c4876a905797b75b62d0655fd2fb0e6ca049fe3640775f418",
        "3956c8e932bea891ecdf4621fcd4c986bb461460edf875717c934797a021a2a7",
        (
            (
                0x18B8044,
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
