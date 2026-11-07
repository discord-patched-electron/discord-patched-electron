// SPDX-License-Identifier: MIT
//! Policy at the native scaler and DRM wait boundaries, independent of their ABI.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PixelLimit {
    Native,
    Invalid,
    Pixels(u32),
}
impl PixelLimit {
    pub fn from_wants(rotation: u8, black: u8, maximum: i32, target: i32, has_target: u8) -> Self {
        if rotation > 1
            || black > 1
            || has_target > 1
            || maximum < 4
            || (has_target == 1 && target < 4)
        {
            return Self::Invalid;
        }
        // Unbounded Source mode retains the native behavior, including its
        // optional target. It does not acquire a new rounding policy.
        if maximum == i32::MAX {
            return Self::Native;
        }
        Self::Pixels(if has_target == 1 {
            maximum.min(target)
        } else {
            maximum
        } as u32)
    }
}
pub fn even_result(
    input: (usize, usize),
    result: (usize, usize),
    limit: u32,
) -> Option<(usize, usize)> {
    let (mut width, mut height) = result;
    if width == 0 || height == 0 || width > u32::MAX as usize || height > u32::MAX as usize {
        return None;
    }
    if input == (0, 0) || width < input.0 || height < input.1 {
        width &= !1;
        height &= !1;
        if width == 0 || height == 0 || width.checked_mul(height)? > limit as usize {
            return None;
        }
    }
    Some((width, height))
}
pub const WAIT_BUDGET_NS: i64 = 5_000_000_000;
/// A small capture preference may use only permissions the user already has.
/// Preserve explicit background priority and any stronger inherited priority.
pub fn capture_nice(current: i32, limit: u64) -> i32 {
    if (-1..=0).contains(&current) && limit >= 22 {
        -2
    } else {
        current
    }
}
pub fn wait_deadline(seconds: i64, nanos: i64) -> Option<i64> {
    if seconds < 0 || !(0..1_000_000_000).contains(&nanos) {
        return None;
    }
    Some(
        seconds
            .saturating_mul(1_000_000_000)
            .saturating_add(nanos)
            .saturating_add(WAIT_BUDGET_NS),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn capture_priority_respects_user_limits_and_existing_intent() {
        for current in -20..=19 {
            assert_eq!(capture_nice(current, 0), current);
            assert_eq!(capture_nice(current, 21), current);
            assert_eq!(
                capture_nice(current, 22),
                if (-1..=0).contains(&current) {
                    -2
                } else {
                    current
                }
            );
        }
    }
    #[test]
    fn optional_targets_never_override_stricter_maximum() {
        assert_eq!(
            PixelLimit::from_wants(0, 0, 1_244_160, 2_073_600, 1),
            PixelLimit::Pixels(1_244_160)
        );
        assert_eq!(
            PixelLimit::from_wants(0, 0, 1_244_160, 921_600, 1),
            PixelLimit::Pixels(921_600)
        );
        assert_eq!(
            PixelLimit::from_wants(0, 0, 1_244_160, 0, 0),
            PixelLimit::Pixels(1_244_160)
        );
        assert_eq!(
            PixelLimit::from_wants(0, 0, i32::MAX, 921_600, 1),
            PixelLimit::Native
        );
        for (rotation, black, max, target, has) in [
            (2, 0, 100, 0, 0),
            (0, 2, 100, 0, 0),
            (0, 0, 3, 0, 0),
            (0, 0, 100, 3, 1),
            (0, 0, 100, 100, 2),
        ] {
            assert_eq!(
                PixelLimit::from_wants(rotation, black, max, target, has),
                PixelLimit::Invalid
            );
        }
    }
    #[test]
    fn reduced_results_fit_after_native_even_rounding() {
        assert_eq!(
            even_result((1920, 1080), (1279, 719), 921_599),
            Some((1278, 718))
        );
        assert_eq!(
            even_result((0, 0), (1487, 837), 1_244_160),
            Some((1486, 836))
        );
        assert_eq!(
            even_result((1919, 1079), (1919, 1079), 2_073_600),
            Some((1919, 1079))
        );
        for result in [(0, 1), (1, 1), (usize::MAX, 2), (1920, 1080)] {
            assert!(even_result((0, 0), result, 921_600).is_none());
        }
    }
    #[test]
    fn monotonic_deadline_saturates_and_rejects_invalid_clock() {
        assert_eq!(wait_deadline(123456, 987654321), Some(123461987654321));
        assert_eq!(wait_deadline(0, 0), Some(WAIT_BUDGET_NS));
        let maximum_start = i64::MAX - WAIT_BUDGET_NS;
        assert_eq!(
            wait_deadline(maximum_start / 1_000_000_000, maximum_start % 1_000_000_000),
            Some(i64::MAX)
        );
        assert_eq!(
            wait_deadline(
                maximum_start / 1_000_000_000,
                maximum_start % 1_000_000_000 + 1
            ),
            Some(i64::MAX)
        );
        assert_eq!(wait_deadline(i64::MAX, 0), Some(i64::MAX));
        for (s, n) in [(-1, 0), (1, -1), (1, 1_000_000_000)] {
            assert_eq!(wait_deadline(s, n), None);
        }
    }
}
