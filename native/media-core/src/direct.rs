// SPDX-License-Identifier: MIT
//! Direct capture ABI 1, encoded explicitly without reading Rust/C padding.
use crate::geometry::Rect;
pub const SIZE: usize = 296;
pub const FDS: usize = 9;
pub const NODE: u32 = 0xffff_fffe;
pub const START: u32 = 1;
pub const FRAME: u32 = 2;
pub const RELEASE: u32 = 3;
pub const RATE: u32 = 4;
pub const END: u32 = 5;
pub const ERROR: u32 = 6;
pub const CURSOR_BITMAP: u32 = 1;
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Image {
    pub width: u32,
    pub height: u32,
    pub format: u32,
    pub planes: u32,
    pub storage: u32,
    pub modifier: u64,
    pub fd: [u32; 4],
    pub offset: [u32; 4],
    pub stride: [u32; 4],
    pub source: Rect,
    pub destination: Rect,
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Packet {
    pub kind: u32,
    pub generation: u64,
    pub sequence: u64,
    pub timestamp_ns: u64,
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub descriptors: u32,
    pub device_major: u32,
    pub device_minor: u32,
    pub image: Image,
    pub cursor: Image,
    pub acquire: u32,
}
fn word(bytes: &[u8], at: usize) -> u32 {
    u32::from_ne_bytes(bytes[at..at + 4].try_into().expect("checked ABI size"))
}
fn long(bytes: &[u8], at: usize) -> u64 {
    u64::from_ne_bytes(bytes[at..at + 8].try_into().expect("checked ABI size"))
}
pub fn header(bytes: &[u8]) -> bool {
    bytes.len() == SIZE
        && word(bytes, 0) == 0x44504544
        && word(bytes, 4) == 1
        && word(bytes, 8) == SIZE as u32
        && (START..=ERROR).contains(&word(bytes, 12))
        && word(bytes, 52) <= FDS as u32
        && word(bytes, 292) == 0
}
fn put(bytes: &mut [u8], at: usize, value: u32) {
    bytes[at..at + 4].copy_from_slice(&value.to_ne_bytes());
}
fn put_long(bytes: &mut [u8], at: usize, value: u64) {
    bytes[at..at + 8].copy_from_slice(&value.to_ne_bytes());
}
fn rectangle(bytes: &mut [u8], at: usize, value: Rect) {
    for (index, value) in [value.x as u32, value.y as u32, value.width, value.height]
        .into_iter()
        .enumerate()
    {
        put(bytes, at + index * 4, value);
    }
}
fn get_rectangle(bytes: &[u8], at: usize) -> Rect {
    Rect {
        x: word(bytes, at) as i32,
        y: word(bytes, at + 4) as i32,
        width: word(bytes, at + 8),
        height: word(bytes, at + 12),
    }
}
impl Image {
    fn write(self, bytes: &mut [u8], at: usize) {
        for (index, value) in [
            self.width,
            self.height,
            self.format,
            self.planes,
            self.storage,
        ]
        .into_iter()
        .enumerate()
        {
            put(bytes, at + index * 4, value);
        }
        put_long(bytes, at + 24, self.modifier);
        for (offset, values) in [(32, self.fd), (48, self.offset), (64, self.stride)] {
            for (index, value) in values.into_iter().enumerate() {
                put(bytes, at + offset + index * 4, value);
            }
        }
        rectangle(bytes, at + 80, self.source);
        rectangle(bytes, at + 96, self.destination);
    }
    fn read(bytes: &[u8], at: usize) -> Self {
        Self {
            width: word(bytes, at),
            height: word(bytes, at + 4),
            format: word(bytes, at + 8),
            planes: word(bytes, at + 12),
            storage: word(bytes, at + 16),
            modifier: long(bytes, at + 24),
            fd: std::array::from_fn(|i| word(bytes, at + 32 + i * 4)),
            offset: std::array::from_fn(|i| word(bytes, at + 48 + i * 4)),
            stride: std::array::from_fn(|i| word(bytes, at + 64 + i * 4)),
            source: get_rectangle(bytes, at + 80),
            destination: get_rectangle(bytes, at + 96),
        }
    }
}
impl Packet {
    pub fn new(kind: u32) -> Self {
        Self {
            kind,
            ..Default::default()
        }
    }
    pub fn bytes(self) -> [u8; SIZE] {
        let mut bytes = [0; SIZE];
        for (at, value) in [
            (0, 0x44504544),
            (4, 1),
            (8, SIZE as u32),
            (12, self.kind),
            (40, self.width),
            (44, self.height),
            (48, self.fps),
            (52, self.descriptors),
            (56, self.device_major),
            (60, self.device_minor),
            (288, self.acquire),
        ] {
            put(&mut bytes, at, value);
        }
        for (at, value) in [
            (16, self.generation),
            (24, self.sequence),
            (32, self.timestamp_ns),
        ] {
            put_long(&mut bytes, at, value);
        }
        self.image.write(&mut bytes, 64);
        self.cursor.write(&mut bytes, 176);
        bytes
    }
    pub fn parse(bytes: &[u8]) -> Option<Self> {
        if !header(bytes) {
            return None;
        };
        Some(Self {
            kind: word(bytes, 12),
            generation: long(bytes, 16),
            sequence: long(bytes, 24),
            timestamp_ns: long(bytes, 32),
            width: word(bytes, 40),
            height: word(bytes, 44),
            fps: word(bytes, 48),
            descriptors: word(bytes, 52),
            device_major: word(bytes, 56),
            device_minor: word(bytes, 60),
            image: Image::read(bytes, 64),
            cursor: Image::read(bytes, 176),
            acquire: word(bytes, 288),
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn abi_round_trip_preserves_signed_rectangles_and_initializes_every_reserved_byte() {
        let image = Image {
            width: 1920,
            height: 1080,
            format: u32::from_le_bytes(*b"XR24"),
            planes: 2,
            storage: 0,
            modifier: 0x123456789abcdef0,
            fd: [0, 1, 0, 0],
            offset: [0, 4096, 0, 0],
            stride: [8192, 4096, 0, 0],
            source: Rect {
                x: 3,
                y: 4,
                width: 1200,
                height: 700,
            },
            destination: Rect {
                x: -7,
                y: -9,
                width: 1280,
                height: 720,
            },
        };
        let packet = Packet {
            kind: FRAME,
            generation: 1234567,
            sequence: 9876543,
            timestamp_ns: u64::MAX,
            width: 1920,
            height: 1080,
            fps: 60,
            descriptors: 3,
            device_major: 226,
            device_minor: 128,
            image,
            cursor: Image::default(),
            acquire: 3,
        };
        let bytes = packet.bytes();
        assert_eq!(Packet::parse(&bytes), Some(packet));
        assert_eq!(&bytes[84..88], &[0; 4]);
        assert_eq!(&bytes[196..200], &[0; 4]);
        assert_eq!(&bytes[292..], &[0; 4]);
        for (at, value) in [(0, 0), (4, 2), (8, 0), (12, 0), (12, 7), (52, 10), (292, 1)] {
            let mut invalid = bytes;
            put(&mut invalid, at, value);
            assert!(Packet::parse(&invalid).is_none());
        }
        assert!(Packet::parse(&bytes[..295]).is_none());
    }
}
