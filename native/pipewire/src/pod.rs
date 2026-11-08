// SPDX-License-Identifier: MIT
//! Bounded SPA format views and immutable aligned offers. Parsing and rate
//! rewriting preserve unrelated fields byte-for-byte and never call C parsers.
use crate::ffi as f;
use std::sync::Arc;

pub const MAX_BODY: usize = 8192;
const PROPERTY_BYTES: usize = 56;
fn word(bytes: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_ne_bytes(
        bytes.get(at..at.checked_add(4)?)?.try_into().ok()?,
    ))
}
fn put(bytes: &mut [u8], at: usize, value: u32) -> Option<()> {
    bytes
        .get_mut(at..at.checked_add(4)?)?
        .copy_from_slice(&value.to_ne_bytes());
    Some(())
}
fn padded(size: usize) -> Option<usize> {
    Some(size.checked_add(7)? & !7)
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Fraction {
    pub numerator: u32,
    pub denominator: u32,
}
impl Fraction {
    pub fn equals_rate(self, fps: u32) -> bool {
        self.denominator != 0
            && u64::from(self.numerator) == u64::from(fps) * u64::from(self.denominator)
    }
    pub fn at_most(self, fps: u32) -> bool {
        self.denominator != 0
            && u64::from(self.numerator) <= u64::from(fps) * u64::from(self.denominator)
    }
    pub fn equivalent(self, other: Self) -> bool {
        self.denominator != 0
            && other.denominator != 0
            && u64::from(self.numerator) * u64::from(other.denominator)
                == u64::from(other.numerator) * u64::from(self.denominator)
    }
}
#[derive(Clone, Copy)]
struct Value<'a> {
    kind: u32,
    body: &'a [u8],
}
impl<'a> Value<'a> {
    fn fractions(self) -> Option<(u32, &'a [u8])> {
        if self.kind == f::DPE_POD_FRACTION && self.body.len() == 8 {
            return Some((f::DPE_POD_NONE, self.body));
        }
        if self.kind != f::DPE_POD_CHOICE
            || self.body.len() < 24
            || word(self.body, 4)? != 0
            || word(self.body, 8)? != 8
            || word(self.body, 12)? != f::DPE_POD_FRACTION
        {
            return None;
        }
        let values = self.body.get(16..)?;
        if !values.len().is_multiple_of(8) {
            return None;
        }
        Some((word(self.body, 0)?, values))
    }
    fn first(self, kind: u32, size: usize) -> Option<&'a [u8]> {
        if self.kind == kind && self.body.len() == size {
            return Some(self.body);
        }
        if self.kind == f::DPE_POD_CHOICE
            && word(self.body, 8)? as usize == size
            && word(self.body, 12)? == kind
        {
            return self.body.get(16..16usize.checked_add(size)?);
        }
        None
    }
}
fn fraction(bytes: &[u8]) -> Option<Fraction> {
    Some(Fraction {
        numerator: word(bytes, 0)?,
        denominator: word(bytes, 4)?,
    })
}
#[derive(Clone, Copy)]
pub struct Format<'a> {
    bytes: &'a [u8],
}
impl<'a> Format<'a> {
    pub fn parse(bytes: &'a [u8]) -> Option<Self> {
        Self::bounded(bytes, MAX_BODY)
    }
    fn bounded(bytes: &'a [u8], maximum: usize) -> Option<Self> {
        let size = word(bytes, 0)? as usize;
        if size < 8
            || size > maximum
            || size.checked_add(8)? != bytes.len()
            || !bytes.len().is_multiple_of(8)
            || word(bytes, 4)? != f::DPE_POD_OBJECT
            || word(bytes, 8)? != f::DPE_POD_FORMAT
        {
            return None;
        }
        let result = Self { bytes };
        // Validate every property, including opaque fields we will preserve.
        let mut offset = 16;
        while offset < bytes.len() {
            let (_, _, end) = result.property_at(offset)?;
            offset = end;
        }
        (offset == bytes.len()).then_some(result)
    }
    fn property_at(self, offset: usize) -> Option<(u32, Value<'a>, usize)> {
        let key = word(self.bytes, offset)?;
        let value = offset.checked_add(8)?;
        let size = word(self.bytes, value)? as usize;
        let start = value.checked_add(8)?;
        let end = start.checked_add(size)?;
        let next = value.checked_add(padded(size.checked_add(8)?)?)?;
        self.bytes.get(end..next)?;
        Some((
            key,
            Value {
                kind: word(self.bytes, value + 4)?,
                body: self.bytes.get(start..end)?,
            },
            next,
        ))
    }
    fn property(self, wanted: u32) -> Option<Value<'a>> {
        let mut offset = 16;
        while offset < self.bytes.len() {
            let (key, value, next) = self.property_at(offset)?;
            if key == wanted {
                return Some(value);
            }
            offset = next;
        }
        None
    }
    pub fn is_raw(self) -> bool {
        self.property(f::DPE_POD_MEDIA_TYPE)
            .and_then(|value| value.first(f::DPE_POD_ID, 4))
            .and_then(|bytes| word(bytes, 0))
            == Some(f::DPE_POD_VIDEO)
            && self
                .property(f::DPE_POD_MEDIA_SUBTYPE)
                .and_then(|value| value.first(f::DPE_POD_ID, 4))
                .and_then(|bytes| word(bytes, 0))
                == Some(f::DPE_POD_RAW)
    }
    pub fn is_offer(self) -> bool {
        self.is_raw() && word(self.bytes, 12) == Some(f::DPE_POD_ENUM_FORMAT)
    }
    pub fn dimensions(self) -> Option<(u32, u32)> {
        let bytes = self
            .property(f::DPE_POD_SIZE)?
            .first(f::DPE_POD_RECTANGLE, 8)?;
        Some((word(bytes, 0)?, word(bytes, 4)?))
    }
    pub fn pixel_format(self) -> Option<u32> {
        word(
            self.property(f::DPE_POD_VIDEO_FORMAT)?
                .first(f::DPE_POD_ID, 4)?,
            0,
        )
    }
    pub fn modifier(self) -> Option<u64> {
        Some(u64::from_ne_bytes(
            self.property(f::DPE_POD_MODIFIER)?
                .first(f::DPE_POD_LONG, 8)?
                .try_into()
                .ok()?,
        ))
    }
    pub fn modifiers(self) -> Option<(bool, Vec<u64>)> {
        let mut offset = 16;
        while offset < self.bytes.len() {
            let (key, value, next) = self.property_at(offset)?;
            if key == f::DPE_POD_MODIFIER {
                let fixation = word(self.bytes, offset + 4)? & f::DPE_POD_DONT_FIXATE != 0;
                if value.kind == f::DPE_POD_LONG && value.body.len() == 8 {
                    return Some((
                        fixation,
                        vec![u64::from_ne_bytes(value.body.try_into().ok()?)],
                    ));
                }
                if value.kind != f::DPE_POD_CHOICE
                    || word(value.body, 4)? != 0
                    || word(value.body, 8)? != 8
                    || word(value.body, 12)? != f::DPE_POD_LONG
                {
                    return None;
                }
                let values = value.body.get(16..)?;
                if !values.len().is_multiple_of(8) || !(1..=257).contains(&(values.len() / 8)) {
                    return None;
                }
                let choice = word(value.body, 0)?;
                if choice == f::DPE_POD_NONE && !fixation {
                    return Some((
                        false,
                        vec![u64::from_ne_bytes(values.get(..8)?.try_into().ok()?)],
                    ));
                }
                if choice != f::DPE_POD_ENUM || values.len() < 16 {
                    return None;
                }
                return Some((
                    fixation,
                    values
                        .as_chunks::<8>()
                        .0
                        .iter()
                        .skip(1)
                        .map(|bytes| u64::from_ne_bytes(*bytes))
                        .collect(),
                ));
            }
            offset = next;
        }
        None
    }
    pub fn rate(self) -> Option<Fraction> {
        let (_, values) = self.property(f::DPE_POD_RATE)?.fractions()?;
        fraction(values)
    }
    pub fn maximum(self) -> Option<Fraction> {
        let (_, values) = self.property(f::DPE_POD_MAX_RATE)?.fractions()?;
        fraction(values)
    }
    pub fn variable(self) -> bool {
        let Some(value) = self.property(f::DPE_POD_RATE) else {
            return false;
        };
        let Some((choice, values)) = value.fractions() else {
            return false;
        };
        let zero = |bytes: &[u8]| {
            fraction(bytes).is_some_and(|rate| rate.numerator == 0 && rate.denominator != 0)
        };
        match choice {
            f::DPE_POD_NONE => values.len() == 8 && zero(values),
            f::DPE_POD_RANGE => {
                values.len() == 24
                    && zero(&values[8..16])
                    && fraction(&values[16..]).is_some_and(|rate| rate.denominator != 0)
            }
            f::DPE_POD_ENUM => values
                .as_chunks::<8>()
                .0
                .iter()
                .skip(1)
                .any(|bytes| zero(bytes)),
            _ => false,
        }
    }
    pub fn owned(self) -> Owned {
        Owned::from_bytes(self.bytes)
    }
    pub fn with_rate(self, fps: u32, preference: bool) -> Option<Owned> {
        if !self.is_offer()
            || !(1..=240).contains(&fps)
            || self.property(f::DPE_POD_MAX_RATE).is_some()
            || preference && (f::DPE_POD_DROP == 0 || !self.variable())
        {
            return None;
        }
        let length = self.bytes.len().checked_add(PROPERTY_BYTES)?;
        let mut bytes = Vec::new();
        bytes.try_reserve_exact(length).ok()?;
        bytes.extend_from_slice(self.bytes);
        bytes.resize(length, 0);
        put(&mut bytes, 0, (length - 8).try_into().ok()?)?;
        let offset = self.bytes.len();
        for (at, value) in [
            (0, f::DPE_POD_MAX_RATE),
            (4, if preference { f::DPE_POD_DROP } else { 0 }),
            (8, 40),
            (12, f::DPE_POD_CHOICE),
            (16, f::DPE_POD_RANGE),
            (24, 8),
            (28, f::DPE_POD_FRACTION),
            (32, fps),
            (36, 1),
            (40, u32::from(!preference)),
            (44, 1),
            (48, if preference { u32::MAX } else { fps }),
            (52, 1),
        ] {
            put(&mut bytes, offset + at, value)?;
        }
        Format::bounded(&bytes, MAX_BODY + PROPERTY_BYTES)?;
        Some(Owned::from_bytes(&bytes))
    }
    pub fn bytes(self) -> &'a [u8] {
        self.bytes
    }
}
/// Shared offers can outlive callbacks and reentrant stream destruction. The
/// backing allocation has the eight-byte alignment required by SPA's C ABI.
#[derive(Clone)]
pub struct Owned(Arc<[u64]>);

impl Owned {
    #[cfg(feature = "source")]
    pub(crate) fn copy_format(bytes: &[u8]) -> Option<Self> {
        Format::parse(bytes)?;
        Some(Self::from_bytes(bytes))
    }
    fn from_bytes(bytes: &[u8]) -> Self {
        let words: Vec<u64> = bytes
            .as_chunks::<8>()
            .0
            .iter()
            .map(|bytes| u64::from_ne_bytes(*bytes))
            .collect();
        Self(words.into())
    }
    pub fn bytes(&self) -> &[u8] {
        // SAFETY: every word is initialized, the allocation is immutable, and
        // byte inspection covers exactly its retained initialized word storage.
        unsafe { std::slice::from_raw_parts(self.0.as_ptr().cast(), self.0.len() * 8) }
    }
    pub fn format(&self) -> Format<'_> {
        Format::bounded(self.bytes(), MAX_BODY + PROPERTY_BYTES).expect("owned validated format")
    }
    pub fn as_ptr(&self) -> *const std::ffi::c_void {
        self.0.as_ptr().cast()
    }
}

#[cfg(test)]
mod modifier_tests {
    use super::*;
    #[test]
    fn native_fixation_retains_choice_storage_but_selects_only_the_default() {
        let offer = crate::source_pods::offer_modifiers(
            crate::source_pods::Format {
                width: 64,
                height: 32,
                format: 1,
                modifier: 0,
            },
            60,
            &[42, 84],
            true,
        )
        .unwrap();
        let mut bytes = offer.bytes().to_vec();
        let view = Format::parse(&bytes).unwrap();
        assert_eq!(view.modifiers(), Some((true, vec![42, 84])));
        let mut offset = 16;
        while view.property_at(offset).unwrap().0 != f::DPE_POD_MODIFIER {
            offset = view.property_at(offset).unwrap().2;
        }
        // This is spa_pod_object_fixate's in-place contract: change the choice
        // kind, retain its complete value storage, clear DONT_FIXATE on the property.
        put(&mut bytes, offset + 4, 0).unwrap();
        put(&mut bytes, offset + 16, f::DPE_POD_NONE).unwrap();
        assert_eq!(
            Format::parse(&bytes).unwrap().modifiers(),
            Some((false, vec![42]))
        );
        put(&mut bytes, offset + 4, f::DPE_POD_DONT_FIXATE).unwrap();
        assert!(Format::parse(&bytes).unwrap().modifiers().is_none());
        put(&mut bytes, offset + 16, f::DPE_POD_ENUM).unwrap();
        put(&mut bytes, offset + 28, f::DPE_POD_ID).unwrap();
        assert!(Format::parse(&bytes).unwrap().modifiers().is_none());
    }
}
