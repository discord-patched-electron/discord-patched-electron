// SPDX-License-Identifier: MIT
//! AV1 Main-tier picture, rate and bitrate limits; not full bitstream certification.
pub fn av1_level(width: u32, height: u32, fps: u32, bitrate: u64) -> i32 {
    const LEVELS: [(i32, u64, u32, u32, u64, u64); 12] = [
        (0, 147456, 2048, 1152, 4423680, 1500000),
        (1, 278784, 2816, 1584, 8363520, 3000000),
        (4, 665856, 4352, 2448, 19975680, 6000000),
        (5, 1065024, 5504, 3096, 31950720, 10000000),
        (8, 2359296, 6144, 3456, 70778880, 12000000),
        (9, 2359296, 6144, 3456, 141557760, 20000000),
        (12, 8912896, 8192, 4352, 267386880, 30000000),
        (13, 8912896, 8192, 4352, 534773760, 40000000),
        (14, 8912896, 8192, 4352, 1069547520, 60000000),
        (16, 35651584, 16384, 8704, 1069547520, 60000000),
        (17, 35651584, 16384, 8704, 2139095040, 100000000),
        (18, 35651584, 16384, 8704, 4278190080, 160000000),
    ];
    if width == 0
        || height == 0
        || fps == 0
        || width > 65536
        || height > 65536
        || fps > 1000
        || bitrate == 0
    {
        return 31;
    }
    let pixels = u64::from(width) * u64::from(height);
    LEVELS
        .into_iter()
        .find(|&(index, picture, w, h, samples, bits)| {
            pixels <= picture
                && width <= w
                && height <= h
                && pixels * u64::from(fps) <= samples
                && bitrate <= bits
                && fps <= if index < 8 { 150 } else { 300 }
        })
        .map_or(31, |level| level.0)
}
