// SPDX-License-Identifier: MIT
//! Bounded AV1 metadata, without parsing or retaining encoded tile pixels.
//! Crop proposal #346 uses metadata type 32. Unknown metadata remains harmless.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Crop {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
}
impl Crop {
    pub fn valid_for(self, width: u32, height: u32) -> bool {
        self.width != 0
            && self.height != 0
            && (self.x | self.y | self.width | self.height) & 1 == 0
            && self.x <= width
            && self.y <= height
            && self.width <= width - self.x
            && self.height <= height - self.y
    }
    pub fn metadata(self) -> Option<[u8; 12]> {
        if !self.valid_for(65536, 65536) || self.x > 65535 || self.y > 65535 {
            return None;
        }
        let width = (self.width - 1).to_be_bytes();
        let height = (self.height - 1).to_be_bytes();
        Some([
            0x2a,
            10,
            32,
            width[2],
            width[3],
            height[2],
            height[3],
            128 | ((self.x >> 9) as u8),
            (self.x >> 1) as u8,
            (((self.x & 1) << 7) | (self.y >> 9)) as u8,
            (self.y >> 1) as u8,
            (((self.y & 1) << 7) | 64) as u8,
        ])
    }
    fn payload(bytes: &[u8]) -> Option<Self> {
        if !matches!(bytes.len(), 5 | 9) {
            return None;
        }
        let mut crop = Self {
            x: 0,
            y: 0,
            width: u32::from(u16::from_be_bytes([bytes[0], bytes[1]])) + 1,
            height: u32::from(u16::from_be_bytes([bytes[2], bytes[3]])) + 1,
        };
        if bytes[4] & 128 != 0 {
            if bytes.len() != 9 || bytes[8] & 127 != 64 {
                return None;
            }
            crop.x = (u32::from(bytes[4] & 127) << 9)
                | (u32::from(bytes[5]) << 1)
                | u32::from(bytes[6] >> 7);
            crop.y = (u32::from(bytes[6] & 127) << 9)
                | (u32::from(bytes[7]) << 1)
                | u32::from(bytes[8] >> 7);
        } else if bytes.len() != 5 || bytes[4] != 64 {
            return None;
        }
        crop.valid_for(65536, 65536).then_some(crop)
    }
}
fn leb(bytes: &[u8]) -> Option<(usize, &[u8])> {
    let mut value = 0usize;
    for (index, &byte) in bytes.iter().take(8).enumerate() {
        let shift = index * 7;
        if shift >= usize::BITS as usize || usize::from(byte & 127) > usize::MAX >> shift {
            return None;
        }
        value |= usize::from(byte & 127) << shift;
        if byte & 128 == 0 {
            return Some((value, &bytes[index + 1..]));
        }
    }
    None
}
struct Obu<'a> {
    kind: u8,
    layer: u8,
    data: &'a [u8],
    start: usize,
}
struct Obus<'a> {
    remaining: &'a [u8],
    offset: usize,
    count: u32,
}
impl<'a> Obus<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self {
            remaining: bytes,
            offset: 0,
            count: 0,
        }
    }
}
impl<'a> Iterator for Obus<'a> {
    type Item = Result<Obu<'a>, ()>;
    fn next(&mut self) -> Option<Self::Item> {
        let (&header, mut payload) = self.remaining.split_first()?;
        let start = self.offset;
        let result = (|| {
            self.count += 1;
            if self.count > 1024 || header & 0x81 != 0 {
                return None;
            }
            let layer = if header & 4 != 0 {
                let (&layer, rest) = payload.split_first()?;
                if layer & 7 != 0 {
                    return None;
                }
                payload = rest;
                layer
            } else {
                0
            };
            let size = if header & 2 != 0 {
                let (size, rest) = leb(payload)?;
                payload = rest;
                size
            } else {
                payload.len()
            };
            if size > payload.len() {
                return None;
            }
            let (data, rest) = payload.split_at(size);
            self.offset += self.remaining.len() - rest.len();
            self.remaining = rest;
            Some(Obu {
                kind: (header >> 3) & 15,
                layer,
                data,
                start,
            })
        })();
        if result.is_none() {
            self.remaining = &[];
        }
        Some(result.ok_or(()))
    }
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct State {
    active: Option<Crop>,
    pending: Option<Crop>,
    sequence: Option<bool>,
}
impl State {
    pub fn read(&mut self, bytes: &[u8]) -> Option<Crop> {
        match self.scan(bytes) {
            Ok(crop) => crop,
            Err(()) => {
                *self = Self::default();
                None
            }
        }
    }
    fn scan(&mut self, bytes: &[u8]) -> Result<Option<Crop>, ()> {
        let mut pending = self.pending;
        let mut frame = false;
        for obu in Obus::new(bytes) {
            let obu = obu?;
            if obu.layer != 0 {
                continue;
            }
            match obu.kind {
                1 => {
                    let first = *obu.data.first().ok_or(())?;
                    *self = Self {
                        sequence: Some(first & 8 != 0),
                        ..Self::default()
                    };
                    pending = None;
                }
                5 => {
                    let (kind, payload) = leb(obu.data).ok_or(())?;
                    if kind == 32 {
                        pending = Some(Crop::payload(payload).ok_or(())?);
                    }
                }
                3 | 6 => {
                    let first = *obu.data.first().ok_or(())?;
                    if frame {
                        return Err(());
                    }
                    frame = true;
                    if self.sequence == Some(true) || first & 0xe0 == 0 {
                        self.active = None;
                    }
                    if self.sequence.is_some()
                        && let Some(crop) = pending
                    {
                        self.active = Some(crop);
                    }
                    pending = None;
                }
                _ => {}
            }
        }
        self.pending = pending;
        Ok(if frame { self.active } else { None })
    }
}
pub fn insertion_position(bytes: &[u8]) -> Option<usize> {
    for obu in Obus::new(bytes) {
        let obu = obu.ok()?;
        if matches!(obu.kind, 3 | 6) {
            return Some(obu.start);
        }
    }
    None
}
#[cfg(test)]
mod tests {
    use super::*;
    const CROP: Crop = Crop {
        x: 32,
        y: 16,
        width: 128,
        height: 128,
    };
    const SEQUENCE: [u8; 3] = [0x0a, 1, 0];
    const KEY: [u8; 3] = [0x32, 1, 0x10];
    const DELTA: [u8; 3] = [0x32, 1, 0x30];
    #[test]
    fn metadata_roundtrip_and_bounds_match_the_wire_proposal() {
        assert_eq!(
            CROP.metadata().unwrap(),
            [0x2a, 10, 32, 0, 127, 0, 127, 128, 16, 0, 8, 64]
        );
        for crop in [
            CROP,
            Crop {
                x: 0,
                y: 0,
                width: 65536,
                height: 65536,
            },
            Crop {
                x: 65534,
                y: 65534,
                width: 2,
                height: 2,
            },
        ] {
            let wire = crop.metadata().unwrap();
            assert_eq!(Crop::payload(&wire[3..]), Some(crop));
        }
        for crop in [
            Crop { x: 33, ..CROP },
            Crop { width: 0, ..CROP },
            Crop { x: 65536, ..CROP },
            Crop {
                width: u32::MAX,
                ..CROP
            },
        ] {
            assert!(crop.metadata().is_none());
        }
        assert!(Crop::payload(&[0, 127, 0, 127, 65]).is_none());
        assert_eq!(
            Crop::payload(&[0, 127, 0, 127, 64]),
            Some(Crop {
                x: 0,
                y: 0,
                width: 128,
                height: 128
            })
        );
    }
    #[test]
    fn pending_metadata_keyframes_layers_and_invalid_packets_expire_state() {
        let metadata = CROP.metadata().unwrap();
        let mut state = State::default();
        assert_eq!(state.read(&SEQUENCE), None);
        assert_eq!(state.read(&metadata), None);
        assert_eq!(state.read(&KEY), Some(CROP));
        assert_eq!(state.read(&DELTA), Some(CROP));
        assert_eq!(state.read(&KEY), None);
        assert_eq!(
            state.read(&[SEQUENCE.as_slice(), &metadata, &KEY].concat()),
            Some(CROP)
        );
        // A temporal/spatial layer must not modify the base layer's crop.
        assert_eq!(state.read(&[0x0e, 8, 1, 0]), None);
        assert_eq!(state.read(&DELTA), Some(CROP));
        assert_eq!(state.read(&[0x32, 1]), None);
        assert_eq!(state, State::default());
        assert_eq!(state.read(&[metadata.as_slice(), &DELTA].concat()), None);
        assert_eq!(
            state.read(&[SEQUENCE.as_slice(), &metadata, &KEY, &DELTA].concat()),
            None
        );
        assert_eq!(state, State::default());
        assert_eq!(
            insertion_position(&[SEQUENCE.as_slice(), &metadata, &KEY].concat()),
            Some(15)
        );
    }
    #[test]
    fn malformed_and_random_bytes_never_escape_geometry_or_packet_bounds() {
        let metadata = CROP.metadata().unwrap();
        for length in 1..metadata.len() {
            let mut state = State::default();
            state.read(&SEQUENCE);
            state.read(&metadata[..length]);
            assert_eq!(state, State::default());
        }
        let mut state = State::default();
        assert_eq!(state.read(&[0x7a, 0].repeat(1025)), None);
        assert_eq!(state, State::default());
        let mut seed = 0x12345678u64;
        let mut bytes = [0u8; 128];
        for length in (0..128).cycle().take(30000) {
            for byte in &mut bytes[..length] {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                *byte = seed as u8;
            }
            if let Some(crop) = state.read(&bytes[..length]) {
                assert!(crop.valid_for(65536, 65536));
            }
            assert!(insertion_position(&bytes[..length]).is_none_or(|at| at < length));
        }
    }
}
