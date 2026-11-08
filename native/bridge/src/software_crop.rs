// SPDX-License-Identifier: MIT
//! dav1d output views retain native allocation ownership. A short registry
//! lookup clones each decoder's owner; decoding holds only its own state lock.
use crate::call_patch::{self, Branch, Module};
use dpe_core::av1_crop::{Crop, State};
use std::{
    collections::HashMap,
    ffi::{CStr, c_char, c_void},
    ptr,
    sync::{
        Arc, Mutex, MutexGuard, OnceLock,
        atomic::{AtomicU64, AtomicUsize, Ordering},
    },
};
const CONTEXTS: usize = 128;
const SNAPSHOTS: usize = 128;
#[repr(C)]
struct Data {
    data: *const u8,
    size: usize,
    reference: *mut c_void,
    timestamp: i64,
}
#[repr(C)]
#[derive(Clone, Copy)]
struct Picture {
    sequence: *mut c_void,
    frame: *mut c_void,
    plane: [*mut u8; 3],
    stride: [isize; 2],
    width: i32,
    height: i32,
    layout: i32,
    depth: i32,
    timestamp: i64,
}
const _: () = assert!(
    std::mem::offset_of!(Data, timestamp) == 24
        && std::mem::offset_of!(Picture, width) == 56
        && std::mem::offset_of!(Picture, timestamp) == 72
);
#[derive(Clone, Copy)]
struct Snapshot {
    ticket: i64,
    timestamp: i64,
    crop: Option<Crop>,
}
struct Decoder {
    state: State,
    frames: [Option<Snapshot>; SNAPSHOTS],
    ticket: i64,
}
impl Default for Decoder {
    fn default() -> Self {
        Self {
            state: State::default(),
            frames: [None; SNAPSHOTS],
            ticket: 0,
        }
    }
}
type Registry = HashMap<usize, Arc<Mutex<Decoder>>>;
static DECODERS: OnceLock<Mutex<Registry>> = OnceLock::new();
static COUNTS: [AtomicU64; 4] = [const { AtomicU64::new(0) }; 4];
static ORIGINAL: [AtomicUsize; 4] = [const { AtomicUsize::new(0) }; 4];
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
static LINKAGE: OnceLock<[usize; 10]> = OnceLock::new();
static INSTALL: Mutex<()> = Mutex::new(());
type Open = unsafe extern "C" fn(*mut *mut c_void, *const c_void) -> i32;
type Send = unsafe extern "C" fn(*mut c_void, *mut Data) -> i32;
type Get = unsafe extern "C" fn(*mut c_void, *mut Picture) -> i32;
type Close = unsafe extern "C" fn(*mut *mut c_void);
fn lock<T>(value: &Mutex<T>) -> MutexGuard<'_, T> {
    value
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
fn registry() -> MutexGuard<'static, Registry> {
    lock(DECODERS.get_or_init(Default::default))
}
fn decoder(context: *mut c_void) -> Option<Arc<Mutex<Decoder>>> {
    registry().get(&(context as usize)).cloned()
}

unsafe extern "C" fn opened(context: *mut *mut c_void, settings: *const c_void) -> i32 {
    // SAFETY: install publishes the exact reviewed open ABI before patching.
    let original =
        unsafe { std::mem::transmute::<usize, Open>(ORIGINAL[0].load(Ordering::Acquire)) };
    // SAFETY: all original caller arguments retain their native ownership.
    let result = unsafe { original(context, settings) };
    if result == 0 && !context.is_null() {
        // SAFETY: successful native open initializes its caller-owned output.
        let key = unsafe { *context } as usize;
        if key != 0 {
            let mut decoders = registry();
            if decoders.len() < CONTEXTS {
                decoders
                    .entry(key)
                    .or_insert_with(|| Arc::new(Mutex::new(Decoder::default())));
            }
        }
    }
    result
}
unsafe extern "C" fn sent(context: *mut c_void, data: *mut Data) -> i32 {
    // SAFETY: install publishes the exact reviewed send ABI before patching.
    let original =
        unsafe { std::mem::transmute::<usize, Send>(ORIGINAL[1].load(Ordering::Acquire)) };
    let entry = decoder(context);
    if data.is_null() || entry.is_none() {
        // SAFETY: preserve native behavior for untracked contexts/arguments.
        return unsafe { original(context, data) };
    }
    // SAFETY: caller owns a live dav1d_data prefix for this synchronous API call.
    let (bytes, size, timestamp) = unsafe { ((*data).data, (*data).size, (*data).timestamp) };
    if bytes.is_null() || size == 0 || size > isize::MAX as usize {
        // SAFETY: invalid/empty packets keep native validation and behavior.
        return unsafe { original(context, data) };
    }
    let entry = entry.expect("tracked decoder");
    let mut entry = lock(&entry);
    let Some(ticket) = entry.ticket.checked_add(1) else {
        // A private identity is never reused after exhaustion. Cropping stops;
        // a native timestamp cannot collide with an earlier private identity.
        entry.frames.fill(None);
        // SAFETY: no metadata is changed for an exhausted decoder.
        return unsafe { original(context, data) };
    };
    entry.ticket = ticket;
    let mut next = entry.state;
    // SAFETY: native dav1d_data owns size initialized bytes until send consumes
    // them. This borrow ends before the original native call can free storage.
    let crop = next.read(unsafe { std::slice::from_raw_parts(bytes, size) });
    // SAFETY: temporarily replace only this live input's public metadata scalar.
    unsafe {
        (*data).timestamp = ticket;
    }
    // SAFETY: caller's original context/data and serialized per-decoder contract.
    let result = unsafe { original(context, data) };
    if result != 0 {
        // SAFETY: dav1d leaves the caller-owned data record live on return. Only
        // restore metadata if it still describes the exact unconsumed bytes.
        unsafe {
            if (*data).data == bytes {
                (*data).timestamp = timestamp;
            }
        }
        return result;
    }
    entry.state = next;
    entry.frames[ticket as usize % SNAPSHOTS] = Some(Snapshot {
        ticket,
        timestamp,
        crop,
    });
    COUNTS[0].fetch_add(1, Ordering::Relaxed);
    result
}
fn offsets(picture: &Picture, crop: Crop) -> Option<[usize; 3]> {
    if picture.width <= 0
        || picture.height <= 0
        || picture.depth != 8
        || !matches!(picture.layout, 1 | 3)
        || !crop.valid_for(picture.width as u32, picture.height as u32)
    {
        return None;
    }
    let mut offsets = [0; 3];
    for (plane, offset) in offsets.iter_mut().enumerate() {
        let divisor = if plane != 0 && picture.layout == 1 {
            2
        } else {
            1
        };
        let stride = picture.stride[usize::from(plane != 0)];
        if picture.plane[plane].is_null()
            || stride < (picture.width as isize + divisor - 1) / divisor
        {
            return None;
        }
        let (x, y) = (
            crop.x as usize / divisor as usize,
            crop.y as usize / divisor as usize,
        );
        *offset = y.checked_mul(stride as usize)?.checked_add(x)?;
        if *offset > isize::MAX as usize
            || (picture.plane[plane] as usize)
                .checked_add(*offset)
                .is_none()
        {
            return None;
        }
    }
    Some(offsets)
}
fn crop_picture(picture: &mut Picture, crop: Crop) -> bool {
    let Some(offsets) = offsets(picture, crop) else {
        return false;
    };
    for (plane, offset) in picture.plane.iter_mut().zip(offsets) {
        *plane = plane.wrapping_add(offset);
    }
    picture.width = crop.width as i32;
    picture.height = crop.height as i32;
    true
}
unsafe extern "C" fn received(context: *mut c_void, picture: *mut Picture) -> i32 {
    // SAFETY: install publishes the exact reviewed get-picture ABI before patching.
    let original =
        unsafe { std::mem::transmute::<usize, Get>(ORIGINAL[2].load(Ordering::Acquire)) };
    // SAFETY: native output storage/ownership are passed through unchanged.
    let result = unsafe { original(context, picture) };
    let entry = if result == 0 && !picture.is_null() {
        decoder(context)
    } else {
        None
    };
    let Some(entry) = entry else {
        return result;
    };
    let entry = lock(&entry);
    // SAFETY: successful native get-picture initializes this ABI prefix. The
    // native allocation/reference tail is never copied, destroyed or modified.
    let picture = unsafe { &mut *picture };
    let index = (picture.timestamp as u64 % SNAPSHOTS as u64) as usize;
    if let Some(frame) = entry.frames[index]
        && frame.ticket == picture.timestamp
        && picture.timestamp > 0
    {
        picture.timestamp = frame.timestamp;
        // One accepted input may produce more than one output picture. Keep its
        // metadata until this bounded ring slot is replaced by a later input.
        // Every output must restore the caller's timestamp independently.
        if let Some(crop) = frame.crop {
            COUNTS[if crop_picture(picture, crop) { 1 } else { 2 }].fetch_add(1, Ordering::Relaxed);
        }
    } else {
        COUNTS[3].fetch_add(1, Ordering::Relaxed);
    }
    result
}
unsafe extern "C" fn closed(context: *mut *mut c_void) {
    if !context.is_null() {
        // SAFETY: caller-owned context pointer remains live until native close.
        let key = unsafe { *context } as usize;
        // Drop state outside the registry lock; retained in-flight owners stay valid.
        let removed = registry().remove(&key);
        drop(removed);
    }
    // SAFETY: install publishes the exact reviewed close ABI before patching.
    let original =
        unsafe { std::mem::transmute::<usize, Close>(ORIGINAL[3].load(Ordering::Acquire)) };
    // SAFETY: caller's context pointer and its native allocation ownership are unchanged.
    unsafe {
        original(context);
    }
}
unsafe fn install(filename: *const c_char, offsets: *const u32) -> Option<()> {
    if filename.is_null() || offsets.is_null() {
        return None;
    }
    let _install = lock(&INSTALL);
    // SAFETY: reviewed C caller supplies a NUL-terminated filename and ten offsets.
    let (filename, offsets) = unsafe {
        (
            CStr::from_ptr(filename),
            std::slice::from_raw_parts(offsets, 10),
        )
    };
    if offsets.contains(&0) {
        return None;
    }
    let module = Module::loaded(filename).ok()?;
    let mut addresses = [0usize; 10];
    for (address, &offset) in addresses.iter_mut().zip(offsets) {
        *address = module.address(offset).ok()?;
    }
    let installed = INSTALLED.load(Ordering::Acquire);
    if installed != 0 {
        return (LINKAGE.get() == Some(&addresses)).then_some(());
    }
    let roles = [0, 1, 2, 3, 3, 3];
    let replacements = [
        opened as *const () as usize,
        sent as *const () as usize,
        received as *const () as usize,
        closed as *const () as usize,
    ];
    let branches: Vec<_> = (0..6)
        .map(|i| Branch {
            site: addresses[i + 4],
            target: addresses[roles[i]],
            opcode: if i == 3 { 0xe9 } else { 0xe8 },
            replacement: replacements[roles[i]],
        })
        .collect();
    // SAFETY: the checked whole-file caller guard and independent branch roles
    // scope these pre-initialization sites. The install mutex serializes writes.
    unsafe {
        call_patch::redirect_group(module, &branches, || {
            for (atomic, &address) in ORIGINAL.iter().zip(&addresses[..4]) {
                atomic.store(address, Ordering::Release);
            }
        })
    }
    .ok()?;
    let _ = LINKAGE.set(addresses);
    INSTALLED.store(addresses[0], Ordering::Release);
    Some(())
}
/// # Safety
/// Filename is NUL-terminated; offsets points to ten initialized reviewed offsets.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_software_av1_install(
    filename: *const c_char,
    offsets: *const u32,
) -> i32 {
    // SAFETY: forwarded C arguments retain the documented input lifetime.
    std::panic::catch_unwind(|| unsafe { install(filename, offsets) })
        .ok()
        .flatten()
        .is_some() as i32
}
/// # Safety
/// Output points to six writable uint64 values.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_software_av1_stats(output: *mut u64) {
    if output.is_null() {
        return;
    }
    let values = [
        u64::from(INSTALLED.load(Ordering::Acquire) != 0),
        registry().len() as u64,
        COUNTS[0].load(Ordering::Relaxed),
        COUNTS[1].load(Ordering::Relaxed),
        COUNTS[2].load(Ordering::Relaxed),
        COUNTS[3].load(Ordering::Relaxed),
    ];
    // SAFETY: caller provides six scalar outputs, all source elements initialized.
    unsafe {
        ptr::copy_nonoverlapping(values.as_ptr(), output, values.len());
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn output_views_validate_all_planes_before_modifying_any_field() {
        let mut pixels = vec![0x5au8; 192 * 160 * 3];
        let original = Picture {
            sequence: ptr::null_mut(),
            frame: ptr::null_mut(),
            plane: [
                pixels.as_mut_ptr(),
                pixels.as_mut_ptr().wrapping_add(192 * 160),
                pixels.as_mut_ptr().wrapping_add(192 * 160 * 2),
            ],
            stride: [192, 192],
            width: 192,
            height: 160,
            layout: 1,
            depth: 8,
            timestamp: 123,
        };
        let crop = Crop {
            x: 32,
            y: 16,
            width: 128,
            height: 128,
        };
        for rejected in [
            Crop { x: 100, ..crop },
            Crop { x: 33, ..crop },
            Crop { width: 0, ..crop },
        ] {
            let mut picture = original;
            assert!(!crop_picture(&mut picture, rejected));
            assert_eq!(picture.plane, original.plane);
            assert_eq!((picture.width, picture.height), (192, 160));
        }
        for (layout, depth, stride) in [(1, 8, 0), (1, 8, -192), (1, 10, 192), (2, 8, 192)] {
            let mut picture = Picture {
                layout,
                depth,
                stride: [192, stride],
                ..original
            };
            assert!(!crop_picture(&mut picture, crop));
            assert_eq!(picture.plane, original.plane);
        }
        let mut picture = original;
        assert!(crop_picture(&mut picture, crop));
        assert_eq!(
            picture.plane[0],
            pixels.as_mut_ptr().wrapping_add(16 * 192 + 32)
        );
        assert_eq!(
            picture.plane[1],
            original.plane[1].wrapping_add(8 * 192 + 16)
        );
        let mut picture = Picture {
            layout: 3,
            ..original
        };
        assert!(crop_picture(&mut picture, crop));
        assert_eq!(
            picture.plane[1],
            original.plane[1].wrapping_add(16 * 192 + 32)
        );
        assert!(pixels.iter().all(|&pixel| pixel == 0x5a));
    }
}
