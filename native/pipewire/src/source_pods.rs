// SPDX-License-Identifier: MIT
//! Bounded, aligned producer negotiation. Shared by the Rust source migration;
//! no variadic C builder or borrowed stack POD crosses a native callback.
use crate::{ffi as f, pod::MAX_BODY};
pub struct Pod(Box<[u64]>);
impl Pod {
    pub fn bytes(&self) -> &[u8] {
        // SAFETY: initialized immutable u64 words contain exactly their byte
        // storage, with eight-byte SPA alignment and no retained mutable alias.
        unsafe { std::slice::from_raw_parts(self.0.as_ptr().cast(), self.0.len() * 8) }
    }
    pub fn as_ptr(&self) -> *const f::spa_pod {
        self.0.as_ptr().cast()
    }
}
struct Builder(Vec<u8>);
impl Builder {
    fn object(kind: u32, parameter: u32) -> Self {
        let mut builder = Self(Vec::with_capacity(512));
        for word in [0, f::DPE_POD_OBJECT, kind, parameter] {
            builder.word(word);
        }
        builder
    }
    fn word(&mut self, value: u32) {
        self.0.extend_from_slice(&value.to_ne_bytes());
    }
    fn property(&mut self, key: u32, flags: u32, kind: u32, value: &[u8]) {
        for word in [key, flags, value.len() as u32, kind] {
            self.word(word);
        }
        self.0.extend_from_slice(value);
        self.0.resize((self.0.len() + 7) & !7, 0);
    }
    fn scalar(&mut self, key: u32, kind: u32, value: u32) {
        self.property(key, 0, kind, &value.to_ne_bytes());
    }
    fn choice(&mut self, key: u32, choice: u32, kind: u32, size: u32, values: &[u32]) {
        let mut bytes = Vec::with_capacity(16 + values.len() * 4);
        for word in [choice, 0, size, kind]
            .into_iter()
            .chain(values.iter().copied())
        {
            bytes.extend_from_slice(&word.to_ne_bytes());
        }
        self.property(key, 0, f::DPE_POD_CHOICE, &bytes);
    }
    fn finish(mut self) -> Option<Pod> {
        if self.0.len() > MAX_BODY + 8 {
            return None;
        }
        let size = u32::try_from(self.0.len() - 8).ok()?;
        self.0[..4].copy_from_slice(&size.to_ne_bytes());
        Some(Pod(self
            .0
            .as_chunks::<8>()
            .0
            .iter()
            .map(|word| u64::from_ne_bytes(*word))
            .collect()))
    }
}
#[derive(Clone, Copy)]
pub struct Format {
    pub width: u32,
    pub height: u32,
    pub format: u32,
    pub modifier: u64,
}
pub fn offer(format: Format, fps: u32) -> Option<Pod> {
    offer_modifiers(format, fps, &[format.modifier], false)
}
pub fn offer_modifiers(format: Format, fps: u32, modifiers: &[u64], fixation: bool) -> Option<Pod> {
    if !(1..=16384).contains(&format.width)
        || !(1..=16384).contains(&format.height)
        || !(1..=240).contains(&fps)
        || format.format == 0
        || modifiers.is_empty()
        || modifiers.len() > 256
    {
        return None;
    }
    let mut builder = Builder::object(f::DPE_POD_FORMAT, f::DPE_POD_ENUM_FORMAT);
    builder.scalar(f::DPE_POD_MEDIA_TYPE, f::DPE_POD_ID, f::DPE_POD_VIDEO);
    builder.scalar(f::DPE_POD_MEDIA_SUBTYPE, f::DPE_POD_ID, f::DPE_POD_RAW);
    builder.scalar(f::DPE_POD_VIDEO_FORMAT, f::DPE_POD_ID, format.format);
    let mut size = [0u8; 8];
    size[..4].copy_from_slice(&format.width.to_ne_bytes());
    size[4..].copy_from_slice(&format.height.to_ne_bytes());
    builder.property(f::DPE_POD_SIZE, 0, f::DPE_POD_RECTANGLE, &size);
    let mut fraction = [0; 8];
    fraction[4..].copy_from_slice(&1u32.to_ne_bytes());
    builder.property(f::DPE_POD_RATE, 0, f::DPE_POD_FRACTION, &fraction);
    builder.choice(
        f::DPE_POD_MAX_RATE,
        f::DPE_POD_RANGE,
        f::DPE_POD_FRACTION,
        8,
        &[fps, 1, 1, 1, fps, 1],
    );
    if fixation {
        let mut choice = Vec::with_capacity(24 + modifiers.len() * 8);
        for word in [f::DPE_POD_ENUM, 0, 8, f::DPE_POD_LONG] {
            choice.extend_from_slice(&word.to_ne_bytes());
        }
        choice.extend_from_slice(&modifiers[0].to_ne_bytes());
        for modifier in modifiers {
            choice.extend_from_slice(&modifier.to_ne_bytes());
        }
        builder.property(
            f::DPE_POD_MODIFIER,
            f::DPE_POD_MANDATORY | f::DPE_POD_DONT_FIXATE,
            f::DPE_POD_CHOICE,
            &choice,
        );
    } else {
        builder.property(
            f::DPE_POD_MODIFIER,
            f::DPE_POD_MANDATORY,
            f::DPE_POD_LONG,
            &modifiers[0].to_ne_bytes(),
        );
    }
    builder.finish()
}
pub fn plain_buffers(planes: u32) -> Option<Vec<Pod>> {
    if !(1..=4).contains(&planes) {
        return None;
    }
    let mut builder = Builder::object(f::DPE_POD_BUFFERS, f::DPE_PARAM_BUFFERS);
    builder.choice(
        f::DPE_POD_BUFFER_COUNT,
        f::DPE_POD_RANGE,
        f::DPE_POD_INT,
        4,
        &[4, 2, 8],
    );
    builder.scalar(f::DPE_POD_BLOCKS, f::DPE_POD_INT, planes);
    builder.choice(
        f::DPE_POD_DATA_TYPE,
        f::DPE_POD_FLAGS,
        f::DPE_POD_INT,
        4,
        &[1 << f::spa_data_type_SPA_DATA_DmaBuf],
    );
    Some(vec![
        builder.finish()?,
        metadata(f::DPE_POD_HEADER, size_of::<f::spa_meta_header>())?,
    ])
}
fn metadata(kind: u32, bytes: usize) -> Option<Pod> {
    let mut builder = Builder::object(f::DPE_POD_META, f::DPE_PARAM_META);
    builder.scalar(f::DPE_POD_META_TYPE, f::DPE_POD_ID, kind);
    builder.scalar(
        f::DPE_POD_META_SIZE,
        f::DPE_POD_INT,
        u32::try_from(bytes).ok()?,
    );
    builder.finish()
}
/// Relay buffers carry plane data plus two sync-object blocks; copy buffers
/// carry one completed linear BGRA plane. Counts/geometry remain bounded.
pub fn buffers(width: u32, height: u32, relay_planes: Option<u32>) -> Option<Vec<Pod>> {
    if !(1..=8192).contains(&width)
        || !(1..=8192).contains(&height)
        || relay_planes.is_some_and(|planes| !(1..=4).contains(&planes))
    {
        return None;
    }
    let mut builder = Builder::object(f::DPE_POD_BUFFERS, f::DPE_PARAM_BUFFERS);
    builder.choice(
        f::DPE_POD_BUFFER_COUNT,
        f::DPE_POD_RANGE,
        f::DPE_POD_INT,
        4,
        &[4, 2, 8],
    );
    builder.scalar(
        f::DPE_POD_BLOCKS,
        f::DPE_POD_INT,
        relay_planes.map_or(1, |planes| planes + 2),
    );
    if relay_planes.is_none() {
        builder.scalar(
            f::DPE_POD_BUFFER_SIZE,
            f::DPE_POD_INT,
            width.checked_mul(height)?.checked_mul(4)?,
        );
        builder.scalar(f::DPE_POD_STRIDE, f::DPE_POD_INT, width.checked_mul(4)?);
    }
    builder.choice(
        f::DPE_POD_DATA_TYPE,
        f::DPE_POD_FLAGS,
        f::DPE_POD_INT,
        4,
        &[1 << f::spa_data_type_SPA_DATA_DmaBuf],
    );
    if relay_planes.is_some() {
        builder.property(
            f::DPE_PARAM_RELAY_TYPE,
            f::DPE_POD_MANDATORY,
            f::DPE_POD_INT,
            &(1u32 << f::DPE_PARAM_RELAY_META).to_ne_bytes(),
        );
    }
    let mut result = Vec::with_capacity(3);
    result.push(builder.finish()?);
    if relay_planes.is_some() {
        result.push(metadata(
            f::DPE_PARAM_RELAY_META,
            size_of::<f::dpe_relay_timeline>(),
        )?);
    }
    result.push(metadata(
        f::DPE_POD_HEADER,
        size_of::<f::spa_meta_header>(),
    )?);
    Some(result)
}
type Apply = unsafe extern "C" fn(*mut std::ffi::c_void, *const *const f::spa_pod, u32) -> i32;
/// # Safety
/// Apply borrows the aligned PODs only during its synchronous call. Native
/// connect/update copies these offers; it must never retain their raw pointers.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_source_format(
    width: u32,
    height: u32,
    format: u32,
    modifier: u64,
    fps: u32,
    data: *mut std::ffi::c_void,
    apply: Option<Apply>,
) -> i32 {
    let (Some(pod), Some(apply)) = (
        offer(
            Format {
                width,
                height,
                format,
                modifier,
            },
            fps,
        ),
        apply,
    ) else {
        return -libc::EINVAL;
    };
    let params = [pod.as_ptr()];
    // SAFETY: the complete immutable POD and pointer array stay owned until
    // the synchronous borrowed callback returns, including native reentry.
    unsafe { apply(data, params.as_ptr(), params.len() as u32) }
}
/// # Safety
/// Same synchronous borrowed callback contract as dpe_source_format. A zero
/// plane count selects the completed BGRA-copy contract, otherwise relay.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_source_buffers(
    width: u32,
    height: u32,
    relay_planes: u32,
    data: *mut std::ffi::c_void,
    apply: Option<Apply>,
) -> i32 {
    let (Some(pods), Some(apply)) = (
        buffers(width, height, (relay_planes != 0).then_some(relay_planes)),
        apply,
    ) else {
        return -libc::EINVAL;
    };
    let mut params = [std::ptr::null(); 3];
    for (output, pod) in params.iter_mut().zip(&pods) {
        *output = pod.as_ptr();
    }
    // SAFETY: all aligned owned PODs outlive the native synchronous update.
    unsafe { apply(data, params.as_ptr(), pods.len() as u32) }
}
