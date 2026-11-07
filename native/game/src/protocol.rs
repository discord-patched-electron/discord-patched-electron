// SPDX-License-Identifier: MIT
//! Explicit 128-byte serialization has no Rust/C padding or unchecked enum.
#[derive(Default, Clone)]
pub struct Packet {
    pub kind: u16,
    pub generation: u64,
    pub sequence: u64,
    pub pts: u64,
    pub width: u32,
    pub height: u32,
    pub format: u32,
    pub flags: u32,
    pub modifier: u64,
    pub strides: [u32; 4],
    pub offsets: [u32; 4],
    pub fps: u32,
    pub nfd: u32,
    pub uuid: [u8; 16],
    pub name: [u8; 16],
}

pub const HELLO: u16 = 1;
pub const TEXTURE: u16 = 2;
pub const FRAME: u16 = 3;
pub const STOP: u16 = 4;
pub const CONTROL: u16 = 5;
pub const RELEASE: u16 = 6;
pub const UNSUPPORTED: u16 = 7;
pub const FLIP: u32 = 1;
pub const CAPTURE: u32 = 2;
pub const SYNC_FD: u32 = 4;
pub const DIRECT: u32 = 8;
/// HELLO capability: CAPTURE controls may use zero FPS to pause admission while
/// retaining the allocation and any outstanding consumer lease.
pub const PAUSE: u32 = 16;
impl Packet {
    /// OBS's packed 128-byte texture advertisement. Its implicit-sync protocol
    /// has no frame sequence; callers supply a new local allocation generation.
    pub fn parse_obs(bytes: &[u8; 128], descriptors: usize, generation: u64) -> Option<Self> {
        let word = |at| u32::from_ne_bytes(bytes[at..at + 4].try_into().expect("fixed OBS offset"));
        let width = word(2);
        let height = word(6);
        if bytes[0] != 11
            || bytes[1] as usize != descriptors
            || !(1..=4).contains(&descriptors)
            || !(1..=8192).contains(&width)
            || !(1..=8192).contains(&height)
            || bytes[58] > 1
            || word(59) != 0
            || generation == 0
        {
            return None;
        }
        let mut packet = Self {
            generation,
            width,
            height,
            format: word(10),
            modifier: u64::from_ne_bytes(bytes[46..54].try_into().ok()?),
            flags: if bytes[58] == 1 { FLIP } else { 0 },
            nfd: descriptors as u32,
            ..Self::new(TEXTURE)
        };
        packet.name[..4].copy_from_slice(&bytes[54..58]);
        for i in 0..descriptors {
            let stride = word(14 + i * 4);
            let offset = word(30 + i * 4);
            if !(1..=131072).contains(&stride) || offset > i32::MAX as u32 {
                return None;
            }
            packet.strides[i] = stride;
            packet.offsets[i] = offset;
        }
        Some(packet)
    }
    pub fn new(kind: u16) -> Self {
        Self {
            kind,
            ..Default::default()
        }
    }
    pub fn bytes(&self) -> [u8; 128] {
        let mut bytes = [0u8; 128];
        bytes[..4].copy_from_slice(&0x44504547u32.to_ne_bytes());
        bytes[4..6].copy_from_slice(&1u16.to_ne_bytes());
        bytes[6..8].copy_from_slice(&self.kind.to_ne_bytes());
        for (at, value) in [
            (8, self.generation),
            (16, self.sequence),
            (24, self.pts),
            (48, self.modifier),
        ] {
            bytes[at..at + 8].copy_from_slice(&value.to_ne_bytes());
        }
        for (at, value) in [
            (32, self.width),
            (36, self.height),
            (40, self.format),
            (44, self.flags),
            (88, self.fps),
            (92, self.nfd),
        ] {
            bytes[at..at + 4].copy_from_slice(&value.to_ne_bytes());
        }
        for index in 0..4 {
            bytes[56 + index * 4..60 + index * 4]
                .copy_from_slice(&self.strides[index].to_ne_bytes());
            bytes[72 + index * 4..76 + index * 4]
                .copy_from_slice(&self.offsets[index].to_ne_bytes());
        }
        bytes[96..112].copy_from_slice(&self.uuid);
        bytes[112..].copy_from_slice(&self.name);
        bytes
    }
    pub fn parse(bytes: &[u8; 128]) -> Option<Self> {
        let word =
            |at| u32::from_ne_bytes(bytes[at..at + 4].try_into().expect("fixed protocol offset"));
        let long =
            |at| u64::from_ne_bytes(bytes[at..at + 8].try_into().expect("fixed protocol offset"));
        let kind = u16::from_ne_bytes(bytes[6..8].try_into().ok()?);
        if word(0) != 0x44504547
            || bytes[4..6] != 1u16.to_ne_bytes()
            || !(HELLO..=UNSUPPORTED).contains(&kind)
        {
            return None;
        }
        Some(Self {
            kind,
            generation: long(8),
            sequence: long(16),
            pts: long(24),
            width: word(32),
            height: word(36),
            format: word(40),
            flags: word(44),
            modifier: long(48),
            strides: std::array::from_fn(|i| word(56 + i * 4)),
            offsets: std::array::from_fn(|i| word(72 + i * 4)),
            fps: word(88),
            nfd: word(92),
            uuid: bytes[96..112].try_into().ok()?,
            name: bytes[112..].try_into().ok()?,
        })
    }
}

#[cfg(test)]
mod obs_tests {
    use super::*;
    #[test]
    fn packed_obs_advertisements_preserve_layout_and_reject_bad_shapes() {
        let mut bytes = [0u8; 128];
        bytes[0] = 11;
        bytes[1] = 1;
        for (at, value) in [
            (2, 1920u32),
            (6, 1080),
            (10, 0x34325241),
            (14, 7680),
            (54, 123),
        ] {
            bytes[at..at + 4].copy_from_slice(&value.to_ne_bytes());
        }
        bytes[46..54].copy_from_slice(&0x0200000028a01f04u64.to_ne_bytes());
        bytes[58] = 1;
        let packet = Packet::parse_obs(&bytes, 1, 7).unwrap();
        assert_eq!(
            (packet.width, packet.height, packet.strides[0], packet.flags),
            (1920, 1080, 7680, FLIP)
        );
        assert_eq!(packet.name[..4], 123u32.to_ne_bytes());
        assert_eq!(packet.modifier, 0x0200000028a01f04);
        for count in [0, 2, 5] {
            assert!(Packet::parse_obs(&bytes, count, 7).is_none());
        }
        assert!(Packet::parse_obs(&bytes, 1, 0).is_none());
        for at in [0, 1, 2, 6, 14, 30, 58, 59] {
            let mut invalid = bytes;
            invalid[at..at + 4].fill(255);
            assert!(Packet::parse_obs(&invalid, 1, 7).is_none());
        }
    }
}
