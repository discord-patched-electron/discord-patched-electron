// SPDX-License-Identifier: MIT
//! Bounded view of the SPA cursor storage produced by DPE's cursor owners.
pub struct Cursor<'a> {
    pub serial: u32,
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub pixels: &'a [u32],
}
impl<'a> Cursor<'a> {
    pub fn parse(words: &'a [u32]) -> Option<Self> {
        if words.len() < 12 || words[0] == 0 || words[6] != 28 || words[7] != 12 {
            return None;
        }
        let (width, height) = (words[8], words[9]);
        if !(1..=256).contains(&width)
            || !(1..=256).contains(&height)
            || words[10] != width * 4
            || words[11] != 20
            || words.len() != 12 + (width * height) as usize
            || words[4] >= width
            || words[5] >= height
        {
            return None;
        }
        Some(Self {
            serial: words[0],
            x: (words[2] as i32).checked_sub(words[4] as i32)?,
            y: (words[3] as i32).checked_sub(words[5] as i32)?,
            width,
            height,
            pixels: &words[12..],
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cursor_storage_is_exact_bounded_and_positions_cannot_overflow() {
        let mut words = vec![1, 0, 12, 20, 1, 1, 28, 12, 2, 2, 8, 20, 0, 1, 2, 3];
        let cursor = Cursor::parse(&words).unwrap();
        assert_eq!(
            (cursor.x, cursor.y, cursor.width, cursor.height),
            (11, 19, 2, 2)
        );
        assert_eq!(cursor.pixels, &[0, 1, 2, 3]);
        for length in 0..words.len() {
            assert!(Cursor::parse(&words[..length]).is_none());
        }
        words[2] = i32::MIN as u32;
        assert!(Cursor::parse(&words).is_none());
        words[2] = 12;
        words[11] = 24;
        assert!(Cursor::parse(&words).is_none());
        words[11] = 20;
        words[8] = u32::MAX;
        assert!(Cursor::parse(&words).is_none());
    }
}
