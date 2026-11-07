// SPDX-License-Identifier: MIT
//! Reviewed native encoder policy. Foreign objects remain borrowed. Bounded
//! Rust maps own only policy records, never native handles or C++ allocations.
use crate::{encoder_adapter as backend, ffi as f};
use dpe_core::{admission::lock, handles::pointer_reserved, levels::av1_level};
use std::{
    cell::RefCell,
    collections::HashMap,
    ffi::{CStr, c_void},
    path::Path,
    ptr,
    sync::{LazyLock, Mutex},
};
pub(super) type Create = unsafe extern "C" fn(
    u32,
    *const c_void,
    *mut c_void,
    *mut c_void,
    *mut c_void,
    *mut *mut c_void,
    f::encoder_session,
    f::encoder_options,
) -> bool;
type Update = unsafe extern "C" fn(*mut c_void, f::encoder_options) -> bool;
type Destroy = unsafe extern "C" fn(*mut c_void);
type Vendor = unsafe extern "C" fn(*mut c_void) -> u32;
type Sequence = unsafe extern "C" fn(*mut c_void, *const c_void, *const c_void) -> *mut c_void;
type Tunings =
    unsafe extern "C" fn(*mut c_void, *const c_void, *const c_void, *const c_void) -> *mut c_void;
type VendorQuery = unsafe extern "C" fn(*mut c_void) -> *const libc::c_char;
type Info = unsafe extern "C" fn(*mut c_void, *const c_void) -> *mut c_void;
type Config = unsafe extern "C" fn(*const c_void) -> *const c_void;
type Configure = unsafe extern "C" fn(*mut c_void, *const c_void);
type Started = unsafe extern "C" fn(*const c_void) -> bool;
type Codec = unsafe extern "C" fn(*const c_void) -> u32;
type Rate = unsafe extern "C" fn(*const c_void, i64) -> OptionalRate;
type Budget = unsafe extern "C" fn(*mut c_void, i64);
type Limit = unsafe extern "C" fn(*mut c_void, u64, *const c_void) -> *mut c_void;
use super::encoder_install::{ALLOCATION, GUARD, LEVELS, QP, QUALITY, STREAM, TRANSITIONS};
macro_rules! original {
    ($group:ident, $index:expr, $ty:ty) => {{
        // SAFETY: installers validate this specific target's ABI and publish
        // immutable originals before redirecting any native call to a wrapper.
        std::mem::transmute::<usize, $ty>($group.original($index))
    }};
}
#[derive(Clone, Copy)]
pub(super) struct Guarded {
    pub dimension: bool,
    pub vbaq: bool,
    pub level: u8,
}
#[derive(Clone, Copy)]
struct Allocation {
    manager: usize,
    scaler: usize,
    budget: Option<u32>,
}
#[derive(Clone, Copy)]
struct Construction {
    fps: u32,
    bitrate: u64,
    level: u8,
    observed: bool,
    vbaq: bool,
}
thread_local! { static CONSTRUCTION: RefCell<Vec<Option<Construction>>> = const { RefCell::new(Vec::new()) }; }
struct Constructing;
impl Constructing {
    fn begin(value: Option<Construction>) -> Option<Self> {
        CONSTRUCTION.with(|state| {
            let mut state = state.borrow_mut();
            if state.len() == 32 {
                return None;
            }
            state.push(value);
            Some(Self)
        })
    }
    fn observed(&self) -> Option<Construction> {
        CONSTRUCTION.with(|state| state.borrow().last().copied().flatten())
    }
}
impl Drop for Constructing {
    fn drop(&mut self) {
        CONSTRUCTION.with(|state| {
            state.borrow_mut().pop();
        });
    }
}
fn constructing(update: impl FnOnce(&mut Construction)) {
    CONSTRUCTION.with(|state| {
        if let Some(Some(value)) = state.borrow_mut().last_mut() {
            update(value);
        }
    });
}
#[derive(Default)]
pub(super) struct State {
    pub guarded: HashMap<usize, Guarded>,
    allocations: HashMap<usize, Allocation>,
    pub counters: [u64; 10],
    pub rejected: (u32, u32),
    pub reason: Option<&'static str>,
    pub hardware: bool,
    pub sequence: (u32, u64, u8),
}
pub(super) static STATE: LazyLock<Mutex<State>> = LazyLock::new(|| Mutex::new(State::default()));
const MAXIMUM: usize = 256;
pub(super) const REJECT_CREATE: usize = 0;
pub(super) const REJECT_UPDATE: usize = 1;
pub(super) const SEQUENCES: usize = 2;
pub(super) const QUALITIES: usize = 3;
pub(super) const QP_TRUST: usize = 4;
pub(super) const BANDWIDTH: usize = 5;
pub(super) const TRANSITION: usize = 6;
pub(super) const QUERIES: usize = 7;
pub(super) const ENVELOPES: usize = 8;
/// # Safety
/// The reviewed callback ABI guarantees an initialized native record covering
/// offset + sizeof(T). Callers use only integer/pointer or byte-array types.
unsafe fn read<T: Copy>(record: *const c_void, offset: usize) -> T {
    // SAFETY: this private helper borrows exactly the caller-guaranteed extent,
    // without assuming alignment or creating a reference to native storage.
    unsafe { ptr::read_unaligned(record.byte_add(offset).cast::<T>()) }
}
/// # Safety
/// The reviewed callback gives the caller exclusive access to these output bytes.
unsafe fn write<T: Copy>(record: *mut c_void, offset: usize, value: T) {
    // SAFETY: one complete writable output field; no retained foreign reference.
    unsafe { ptr::write_unaligned(record.byte_add(offset).cast::<T>(), value) };
}
fn count(index: usize) {
    lock(&STATE).counters[index] += 1;
}
fn reject(dimensions: f::encoder_dimensions, update: bool, reason: &'static str) {
    let mut state = lock(&STATE);
    state.counters[usize::from(update)] += 1;
    state.rejected = (dimensions.width, dimensions.height);
    state.reason = Some(reason);
    drop(state);
    eprintln!(
        "DPE encoder: {reason} at {}x{}; using native codec fallback.",
        dimensions.width, dimensions.height
    );
}
fn changes_size(dimensions: f::encoder_dimensions) -> bool {
    dimensions.width != 0
        && dimensions.height != 0
        && (dimensions.width & 63 != 0 || dimensions.height & 15 != 0)
}
pub(super) fn all_amd_vcn4(root: &Path) -> bool {
    fn number(path: &Path, radix: u32) -> Option<u32> {
        let value = std::fs::read_to_string(path).ok()?;
        u32::from_str_radix(value.trim().trim_start_matches("0x"), radix).ok()
    }
    fn directories(path: &Path) -> Option<Vec<std::path::PathBuf>> {
        let mut result = Vec::new();
        for entry in std::fs::read_dir(path).ok()?.take(65) {
            if result.len() == 64 {
                return None;
            }
            result.push(entry.ok()?.path());
        }
        Some(result)
    }
    let Some(devices) = directories(root) else {
        return false;
    };
    let mut found = false;
    for device in devices {
        if !device
            .file_name()
            .is_some_and(|name| name.as_encoded_bytes().starts_with(b"renderD"))
        {
            continue;
        }
        let Some(vendor) = number(&device.join("device/vendor"), 16) else {
            return false;
        };
        if vendor != 0x1002 {
            continue;
        }
        found = true;
        let Some(dies) = directories(&device.join("device/ip_discovery/die")) else {
            return false;
        };
        let mut instances = 0;
        for die in dies {
            let Some(units) = directories(&die.join("UVD")) else {
                return false;
            };
            for unit in units {
                if number(&unit.join("major"), 10) != Some(4) {
                    return false;
                }
                instances += 1;
            }
        }
        if instances == 0 {
            return false;
        }
    }
    found
}
#[cfg(not(test))]
fn hardware() -> bool {
    all_amd_vcn4(Path::new("/sys/class/drm"))
}
#[cfg(test)]
fn hardware() -> bool {
    tests::VCN4.with(std::cell::Cell::get)
}
#[cfg(test)]
#[path = "encoder_guard_tests.rs"]
mod tests;
pub(super) unsafe extern "C" fn create(
    kind: u32,
    mut frame: *const c_void,
    context: *mut c_void,
    encoded: *mut c_void,
    dropped: *mut c_void,
    output: *mut *mut c_void,
    session: f::encoder_session,
    options: f::encoder_options,
) -> bool {
    #[cfg(feature = "capture-gpu")]
    let direct = crate::direct_gpu::dpe_direct_frame_owned(frame);
    #[cfg(not(feature = "capture-gpu"))]
    let direct = false;
    if pointer_reserved(frame as usize) && !direct {
        if !output.is_null() {
            // SAFETY: native create supplies its complete writable output slot.
            unsafe { output.write(ptr::null_mut()) };
        }
        return false;
    }
    #[cfg(feature = "capture-gpu")]
    {
        // SAFETY: unchanged reviewed create arguments; adapter validates owned
        // direct frames and consumes no unrelated opaque native object.
        let result = unsafe {
            backend::dpe_direct_encoder_create(
                kind, frame, context, encoded, dropped, output, session, options,
            )
        };
        if result >= 0 {
            return result != 0;
        }
    }
    if direct {
        frame = ptr::null();
    }
    // SAFETY: native create ABI is forwarded exactly, including by-value padding.
    let result = unsafe {
        backend::dpe_hevc_create(kind, context, encoded, dropped, output, session, options)
    };
    if result >= 0 {
        return result != 0;
    }
    let limits = Construction {
        fps: session.fps.max(options.fps),
        bitrate: u64::from(options.bitrate_maximum.max(options.bitrate_target)),
        level: 31,
        observed: false,
        vbaq: false,
    };
    let Some(construction) = Constructing::begin(matches!(kind, 5 | 6).then_some(limits)) else {
        return false;
    };
    // SAFETY: immutable original ABI was validated before redirect publication;
    // no state/TLS borrow spans native reentry or its nested callbacks.
    let result = unsafe {
        original!(GUARD, 0, Create)(
            kind, frame, context, encoded, dropped, output, session, options,
        )
    };
    let limits = construction.observed().unwrap_or(limits);
    drop(construction);
    if !result || output.is_null() {
        return result;
    }
    // SAFETY: successful native create initializes its output slot.
    let handle = unsafe { output.read() };
    if handle.is_null() {
        return result;
    }
    let av1 = session.codec == 2 && matches!(kind, 5 | 6);
    if av1 && LEVELS.active() && !limits.observed {
        // SAFETY: failed policy admission retires this fresh native object and
        // clears the caller-owned output before selecting any alternate backend.
        unsafe {
            original!(GUARD, 2, Destroy)(handle);
            output.write(ptr::null_mut());
        }
        reject(options.dimensions, false, "av1-level-unverified");
        return false;
    }
    // SAFETY: successful native create returned this live native handle.
    let dimension = av1 && unsafe { original!(GUARD, 3, Vendor)(handle) } == 1 && hardware();
    if dimension {
        lock(&STATE).hardware = true;
        let mut aligned = ptr::null_mut();
        // SAFETY: temporary native output slot and unchanged create contract.
        let result = unsafe {
            backend::dpe_av1_create(
                kind,
                context,
                encoded,
                dropped,
                &mut aligned,
                session,
                options,
            )
        };
        if result > 0 && !aligned.is_null() {
            // SAFETY: retire exactly the original native handle; replacement
            // remains owned by the native caller through its output slot.
            unsafe {
                original!(GUARD, 2, Destroy)(handle);
                output.write(aligned);
            }
            return true;
        }
    }
    let rejection =
        (dimension && changes_size(options.dimensions)).then_some("amd-av1-coded-size-padding");
    if let Some(reason) = rejection {
        // SAFETY: live original handle and caller-owned output slot.
        unsafe {
            original!(GUARD, 2, Destroy)(handle);
            output.write(ptr::null_mut());
        }
        reject(options.dimensions, false, reason);
        return false;
    }
    if dimension || limits.observed || limits.vbaq {
        let mut state = lock(&STATE);
        if state.guarded.len() == MAXIMUM || state.guarded.contains_key(&(handle as usize)) {
            drop(state);
            // SAFETY: admission failed before publishing ownership; retire the
            // fresh native object outside the policy lock and clear output.
            unsafe {
                original!(GUARD, 2, Destroy)(handle);
                output.write(ptr::null_mut());
            }
            return false;
        }
        state.guarded.insert(
            handle as usize,
            Guarded {
                dimension,
                vbaq: limits.vbaq,
                level: limits.level,
            },
        );
    }
    result
}
pub(super) unsafe extern "C" fn update(handle: *mut c_void, options: f::encoder_options) -> bool {
    if pointer_reserved(handle as usize) || backend::dpe_hevc_owned(handle) {
        return backend::dpe_hevc_update(handle, options);
    }
    let guarded = lock(&STATE).guarded.get(&(handle as usize)).copied();
    if guarded.is_some_and(|entry| entry.dimension) && changes_size(options.dimensions) {
        reject(options.dimensions, true, "amd-av1-coded-size-padding");
        return false;
    }
    let level = guarded.map_or(31, |entry| entry.level);
    let bitrate = u64::from(options.bitrate_target.max(options.bitrate_maximum));
    if level != 31
        && options.fps != 0
        && bitrate != 0
        && options.dimensions.width != 0
        && options.dimensions.height != 0
        && av1_level(
            options.dimensions.width,
            options.dimensions.height,
            options.fps,
            bitrate,
        ) > i32::from(level)
    {
        reject(options.dimensions, true, "av1-level-limit");
        return false;
    }
    // SAFETY: borrowed live native handle and unchanged by-value options ABI.
    unsafe { original!(GUARD, 1, Update)(handle, options) }
}
pub(super) unsafe extern "C" fn destroy(handle: *mut c_void) {
    if backend::dpe_hevc_destroy(handle) || pointer_reserved(handle as usize) {
        return;
    }
    lock(&STATE).guarded.remove(&(handle as usize));
    // SAFETY: caller retires its native handle exactly once, outside policy lock.
    unsafe { original!(GUARD, 2, Destroy)(handle) };
}
pub(super) unsafe extern "C" fn sequence(
    output: *mut c_void,
    config: *const c_void,
    header: *const c_void,
) -> *mut c_void {
    // SAFETY: reviewed three-pointer constructor ABI; native owns all records.
    let result = unsafe { original!(LEVELS, 0, Sequence)(output, config, header) };
    // SAFETY: reviewed config is 0x50 bytes; initialized output contains the
    // sequence dimensions at 0x2d4 and first operating-point level at 0x36.
    let (mut fps, rate, dimensions) = unsafe {
        (
            read::<u32>(config, 0x30),
            read::<u32>(config, 0x18),
            read::<[u16; 2]>(output, 0x2d4),
        )
    };
    let mut bitrate = match rate {
        // SAFETY: same initialized complete native config, rate-specific field.
        0 => unsafe { read::<u64>(config, 0x20) },
        // SAFETY: same config's VBR maximum field.
        1 => unsafe { read::<u64>(config, 0x28) },
        _ => 0,
    };
    constructing(|limits| {
        fps = fps.max(limits.fps);
        if rate <= 1 {
            bitrate = bitrate.max(limits.bitrate);
        }
    });
    let level = av1_level(
        u32::from(dimensions[0]) + 1,
        u32::from(dimensions[1]) + 1,
        fps,
        bitrate,
    ) as u8;
    // SAFETY: native constructor initialized this writable output byte.
    unsafe { write(output, 0x36, level) };
    constructing(|limits| {
        limits.observed = true;
        limits.level = level;
    });
    let mut state = lock(&STATE);
    state.counters[SEQUENCES] += 1;
    state.sequence = (fps, bitrate, level);
    result
}
pub(super) unsafe extern "C" fn tunings(
    output: *mut c_void,
    display: *const c_void,
    session: *const c_void,
    options: *const c_void,
) -> *mut c_void {
    // SAFETY: forward exact reviewed native output/config/display ABI.
    let result = unsafe { original!(QUALITY, 0, Tunings)(output, display, session, options) };
    // SAFETY: the initialized native Tunings is 0x40 bytes and Session 0x38.
    if result != output
        // SAFETY: native initialized the reviewed Tunings and Session fields.
        || unsafe {
            read::<u8>(session, 0x32) != 0
                || read::<u32>(output, 0x10) != 1
                || read::<u32>(output, 0x14) != 0
        }
    {
        return result;
    }
    // SAFETY: reviewed Display first word is VADisplay, borrowed for this call.
    let vendor = unsafe { original!(QUALITY, 1, VendorQuery)(read::<*mut c_void>(display, 0)) };
    if vendor.is_null() {
        return result;
    }
    // SAFETY: VAAPI returns a live terminated driver description.
    let vendor = unsafe { CStr::from_ptr(vendor) }.to_bytes();
    let contains = |needle: &[u8]| vendor.windows(needle.len()).any(|part| part == needle);
    if !contains(b"Mesa Gallium driver ") || !(contains(b"AMD") || contains(b"ATI")) {
        return result;
    }
    // SAFETY: owned native output's engaged optional preset; no other field changes.
    unsafe { write(output, 0x14, 0x13u32) };
    constructing(|limits| limits.vbaq = true);
    count(QUALITIES);
    result
}
unsafe fn metadata(info: *const c_void) -> bool {
    // SAFETY: native callbacks supply initialized EncoderInfo (0x110 bytes).
    unsafe {
        read::<u8>(info, 8) == 1
            && read::<u8>(info, 0xf0) == 0
            && read::<u8>(info, 0xf1) == 1
            && ((read::<u8>(info, 0x2f) == 10 && read::<[u8; 10]>(info, 0x18) == *b"amd: vaapi")
                || (read::<u8>(info, 0x2f) == 12
                    && read::<[u8; 12]>(info, 0x18) == *b"intel: vaapi"))
    }
}
fn managed_codec(codec: u32) -> bool {
    codec == 4 || (matches!(codec, 3 | 5) && backend::dpe_hevc_active())
}
pub(super) unsafe extern "C" fn trusted_info(
    output: *mut c_void,
    encoder: *const c_void,
) -> *mut c_void {
    // SAFETY: forward the reviewed EncoderInfo output ABI unchanged.
    let result = unsafe { original!(QP, 0, Info)(output, encoder) };
    if result != output {
        return result;
    }
    // SAFETY: RustEncoder contains its native pointer directly after its vptr.
    let handle = unsafe { read::<*mut c_void>(encoder, 8) };
    let owned = backend::dpe_hevc_owned(handle);
    // SAFETY: native query initialized this complete EncoderInfo optional field.
    if !owned && unsafe { read::<u8>(output, 0xf1) } != 0 {
        return result;
    }
    let eligible = owned
        || lock(&STATE)
            .guarded
            .get(&(handle as usize))
            .is_some_and(|entry| entry.vbaq);
    if eligible {
        // SAFETY: caller exclusively owns native metadata output for this query.
        unsafe {
            write(output, 0xf0, [0u8, 1]);
        }
        count(QP_TRUST);
    }
    result
}
static EXPLICIT_GATE: [u8; 0xca] = {
    let mut result = [0; 0xca];
    result[0xc9] = 1;
    result
};
pub(super) unsafe extern "C" fn bandwidth_config(
    settings: *const c_void,
    info: *const c_void,
) -> *const c_void {
    // SAFETY: reviewed accessor returns native configuration borrowed for this call.
    let result = unsafe { original!(QP, 1, Config)(settings) };
    // SAFETY: native config/metadata have the reviewed initialized extents.
    if unsafe { read::<u8>(result, 0xc9) } != 0 || !unsafe { metadata(info) } {
        return result;
    }
    let mut state = lock(&STATE);
    let vbaq = state.guarded.values().any(|entry| entry.vbaq);
    if vbaq {
        state.counters[BANDWIDTH] += 1;
    }
    drop(state);
    if vbaq || backend::dpe_hevc_active() {
        EXPLICIT_GATE.as_ptr().cast()
    } else {
        result
    }
}
fn forget(manager: Option<usize>, scaler: Option<usize>) {
    lock(&STATE)
        .allocations
        .retain(|_, entry| manager != Some(entry.manager) && scaler != Some(entry.scaler));
}
unsafe fn bind_allocation(manager: *mut c_void, info: *const c_void) {
    // SAFETY: reviewed manager owns settings at 0x268 and a resource at 0x48;
    // callbacks borrow all related native objects through this synchronous call.
    let resource = unsafe {
        if !metadata(info)
            || !managed_codec(original!(TRANSITIONS, 3, Codec)(manager.byte_add(0x268)))
        {
            return;
        }
        read::<*mut c_void>(manager, 0x48)
    };
    // SAFETY: a nonnull manager-owned resource has the reviewed Started ABI.
    if resource.is_null() || !unsafe { original!(TRANSITIONS, 2, Started)(resource) } {
        return;
    }
    // SAFETY: a started resource owns its initialized scaler at 0x60.
    let scaler = unsafe { read::<usize>(resource, 0x60) };
    if scaler == 0 {
        return;
    }
    // SAFETY: this complete manager record includes its optional budget fields.
    let (budget, present) = unsafe { (read::<u32>(manager, 0xec), read::<u8>(manager, 0xf0) == 1) };
    let active = backend::dpe_hevc_active();
    let mut state = lock(&STATE);
    if state
        .allocations
        .get(&scaler)
        .is_some_and(|entry| entry.manager == manager as usize)
    {
        return;
    }
    state
        .allocations
        .retain(|_, entry| entry.manager != manager as usize);
    if (active || state.guarded.values().any(|entry| entry.vbaq))
        && state.allocations.len() < MAXIMUM
    {
        state.allocations.insert(
            scaler,
            Allocation {
                manager: manager as usize,
                scaler,
                budget: (present && budget != 0).then_some(budget),
            },
        );
    }
}
unsafe fn transient(manager: *mut c_void, info: *const c_void) -> bool {
    // SAFETY: native manager and metadata records cover these reviewed offsets.
    let (degradation, codec, resource, unknown) = unsafe {
        (
            read::<u32>(manager, 0x78),
            original!(TRANSITIONS, 3, Codec)(manager.byte_add(0x268)),
            read::<*const c_void>(manager, 0x48),
            read::<u8>(info, 0xf1) == 0
                && read::<u8>(info, 0x2f) == 7
                && read::<[u8; 7]>(info, 0x18) == *b"unknown",
        )
    };
    if !matches!(degradation, 1 | 3) || !unknown || resource.is_null() {
        return false;
    }
    let owned = codec == 4
        || (matches!(codec, 3 | 5)
            && lock(&STATE)
                .allocations
                .values()
                .any(|entry| entry.manager == manager as usize));
    // SAFETY: the manager retains this resource through the native callback.
    if !owned || !unsafe { original!(TRANSITIONS, 2, Started)(resource) } {
        return false;
    }
    count(TRANSITION);
    true
}
pub(super) unsafe extern "C" fn configure_quality(manager: *mut c_void, info: *const c_void) {
    // SAFETY: reviewed manager/metadata callback ABI remains borrowed unchanged.
    unsafe {
        if !transient(manager, info) {
            original!(TRANSITIONS, 0, Configure)(manager, info);
        }
    }
}
pub(super) unsafe extern "C" fn configure_bandwidth(manager: *mut c_void, info: *const c_void) {
    // SAFETY: reviewed manager/metadata callback ABI, no Rust lock spans calls.
    unsafe {
        if transient(manager, info) {
            return;
        }
        if !metadata(info) {
            forget(Some(manager as usize), None);
        }
        original!(TRANSITIONS, 1, Configure)(manager, info);
        bind_allocation(manager, info);
    }
}
#[repr(C)]
#[derive(Default)]
pub(super) struct OptionalRate {
    pub value: i64,
    pub present: u64,
}
pub(super) unsafe extern "C" fn allocation_rate(
    statistics: *const c_void,
    now: i64,
) -> OptionalRate {
    let owned = (statistics as usize).checked_sub(0x20).and_then(|scaler| {
        let mut state = lock(&STATE);
        let entry = state.allocations.get(&scaler).copied()?;
        state.counters[QUERIES] += 1;
        Some(OptionalRate {
            value: i64::from(entry.budget.unwrap_or(0)),
            present: u64::from(entry.budget.is_some()),
        })
    });
    // SAFETY: only the reviewed local query is redirected. Unowned native
    // statistics keep the original ABI and measured rate unchanged.
    owned.unwrap_or_else(|| unsafe { original!(ALLOCATION, 0, Rate)(statistics, now) })
}
pub(super) unsafe extern "C" fn target_budget(manager: *mut c_void, budget: i64) {
    // SAFETY: original native manager callback runs before policy synchronization.
    unsafe { original!(ALLOCATION, 1, Budget)(manager, budget) };
    for entry in lock(&STATE)
        .allocations
        .values_mut()
        .filter(|entry| entry.manager == manager as usize)
    {
        entry.budget = u32::try_from(budget).ok().filter(|budget| *budget != 0);
    }
}
pub(super) unsafe extern "C" fn scaler_destroy(scaler: *mut c_void) {
    forget(None, Some(scaler as usize));
    // SAFETY: caller retires this scaler; policy ownership ends before native deletion.
    unsafe { original!(ALLOCATION, 2, Destroy)(scaler) };
}
pub(super) unsafe extern "C" fn scaler_delete(scaler: *mut c_void) {
    forget(None, Some(scaler as usize));
    // SAFETY: exact distinct native deleting-destructor ABI validated by installer.
    unsafe { original!(ALLOCATION, 3, Destroy)(scaler) };
}
#[repr(C, align(16))]
struct Metadata([u8; 0x110]);
pub(super) unsafe extern "C" fn stream_limit(
    output: *mut c_void,
    pixels: u64,
    limits: *const c_void,
    owner: *const c_void,
) -> *mut c_void {
    // SAFETY: trampoline preserves the original three arguments and adds the
    // reviewed live VideoStreamEncoder pointer from R12 as the fourth argument.
    let result = unsafe { original!(STREAM, 0, Limit)(output, pixels, limits) };
    // SAFETY: native optional limits output and encoder owner have reviewed extents.
    let eligible = unsafe {
        result == output
            && read::<u8>(output, 16) != 0
            && managed_codec(read::<u32>(owner, 0x118))
            && read::<u32>(owner, 0x1a0) == 1
    };
    if !eligible {
        return result;
    }
    // SAFETY: owner retains its active encoder; its vtable includes GetEncoderInfo.
    let encoder = unsafe { read::<*const c_void>(owner, 0x1e8) };
    if encoder.is_null() {
        return result;
    }
    // SAFETY: this native vtable/slot belongs to the reviewed live encoder class.
    let query = unsafe { read::<usize>(read::<*const c_void>(encoder, 0), 0x60) };
    // SAFETY: exact reviewed GetEncoderInfo sret signature.
    let query = unsafe { std::mem::transmute::<usize, Info>(query) };
    let mut info = Metadata([0; 0x110]);
    let pointer = info.0.as_mut_ptr().cast();
    // SAFETY: aligned complete local sret storage; native object remains borrowed.
    if unsafe { query(pointer, encoder) } != pointer {
        return result;
    }
    // SAFETY: successful query initialized EncoderInfo and its vector fields.
    let compatible = unsafe {
        metadata(pointer) && read::<usize>(pointer, 0xb0) == read::<usize>(pointer, 0xb8)
    };
    // SAFETY: invoke the reviewed destructor exactly once for native sret members.
    unsafe { original!(STREAM, 1, Destroy)(pointer) };
    if compatible
        && (backend::dpe_hevc_active() || lock(&STATE).guarded.values().any(|entry| entry.vbaq))
    {
        // SAFETY: only disengage this trivial local optional envelope; native
        // negotiated bitrate, codec limits and scaler resources remain owned.
        unsafe { write(output, 16, 0u8) };
        count(ENVELOPES);
    }
    result
}
