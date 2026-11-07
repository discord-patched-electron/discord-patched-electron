// SPDX-License-Identifier: MIT
//! Checked dimensions, stream constraints and monotonic timestamps. Standard
//! level limits match FFmpeg's H.265 tables and AV1 Annex A (Main tier).
pub fn dimensions(width: u32, height: u32) -> bool {
    (128..=8192).contains(&width)
        && (128..=8192).contains(&height)
        && (width | height) & 1 == 0
        && u64::from(width) * u64::from(height) <= 8192 * 4320
}
pub fn options(width: u32, height: u32, fps: u32, target: u32, maximum: u32) -> bool {
    dimensions(width, height) && fps <= 240 && target <= maximum && maximum <= i32::MAX as u32 / 2
}
pub fn timestamp(previous: i64, requested: u64) -> Option<i64> {
    let requested = i64::try_from(requested).unwrap_or(0);
    if requested > previous {
        Some(requested)
    } else {
        previous.checked_add(1)
    }
}
pub fn hevc_level(width: u32, height: u32, fps: u32, bitrate: u64) -> i32 {
    const LEVELS: [(i32, u64, u64, u64); 13] = [
        (30, 36864, 552960, 128000),
        (60, 122880, 3686400, 1500000),
        (63, 245760, 7372800, 3000000),
        (90, 552960, 16588800, 6000000),
        (93, 983040, 33177600, 10000000),
        (120, 2228224, 66846720, 12000000),
        (123, 2228224, 133693440, 20000000),
        (150, 8912896, 267386880, 25000000),
        (153, 8912896, 534773760, 40000000),
        (156, 8912896, 1069547520, 60000000),
        (180, 35651584, 1069547520, 60000000),
        (183, 35651584, 2139095040, 120000000),
        (186, 35651584, 4278190080, 240000000),
    ];
    let (width, height) = (u64::from(width), u64::from(height));
    let pixels = width * height;
    let Some(samples) = pixels.checked_mul(u64::from(fps)) else {
        return 0;
    };
    if pixels == 0 || fps == 0 {
        return 0;
    }
    LEVELS
        .into_iter()
        .find(|&(_, picture, rate, bits)| {
            pixels <= picture
                && samples <= rate
                && width * width <= picture * 8
                && height * height <= picture * 8
                && bitrate <= bits
        })
        .map_or(0, |level| level.0)
}
pub fn av1_level(width: u32, height: u32, fps: u32, bitrate: u64) -> i32 {
    dpe_core::levels::av1_level(width, height, fps, bitrate)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn geometry_and_timestamps_cannot_wrap_or_create_zero_sized_frames() {
        assert!(options(1920, 1080, 240, 24_000_000, 24_000_000));
        assert!(options(1280, 720, 0, 0, 0));
        for (w, h, f, t, m) in [
            (0, 720, 60, 1, 1),
            (1921, 1080, 60, 1, 1),
            (8192, 8192, 60, 1, 1),
            (1280, 720, 241, 1, 1),
            (1280, 720, 60, 2, 1),
            (1280, 720, 60, 1, u32::MAX),
        ] {
            assert!(!options(w, h, f, t, m));
        }
        assert_eq!(timestamp(10, 0), Some(11));
        assert_eq!(timestamp(10, u64::MAX), Some(11));
        assert_eq!(timestamp(i64::MAX, u64::MAX), None);
    }
    #[test]
    fn levels_include_rate_bitrate_and_lifetime_geometry() {
        assert_eq!(hevc_level(1920, 1080, 30, 9_000_000), 120);
        assert_eq!(hevc_level(1920, 1080, 60, 9_000_000), 123);
        assert_eq!(hevc_level(1920, 1080, 120, 9_000_000), 150);
        assert_eq!(hevc_level(3840, 2160, 60, 9_000_000), 153);
        assert_eq!(hevc_level(u32::MAX, u32::MAX, u32::MAX, 9_000_000), 0);
        assert_eq!(av1_level(1280, 720, 60, 6_000_000), 8);
        assert_eq!(av1_level(1280, 720, 240, 24_000_000), 12);
        assert_eq!(av1_level(0, 720, 60, 6_000_000), 31);
    }
}
