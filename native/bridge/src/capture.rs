// SPDX-License-Identifier: MIT
//! Native PipeWire queue policy. Borrowed metadata never outlives its callback;
//! unknown control/synchronization records preserve native FIFO ownership.
use crate::{
    call_patch::{self, Branch, Module},
    node::{self, Env, Info, Value},
};
use dpe_core::pacing::Pacer;
use std::{
    cell::RefCell,
    ffi::{CStr, CString, c_void},
    ptr,
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    },
};
mod f {
    #![allow(
        non_upper_case_globals,
        non_camel_case_types,
        non_snake_case,
        dead_code,
        unused_imports,
        clippy::all,
        clippy::undocumented_unsafe_blocks,
        unsafe_op_in_unsafe_fn
    )]
    include!(concat!(env!("OUT_DIR"), "/capture.rs"));
}
const LIMIT: usize = 32;
const HISTOGRAM: usize = 24;
#[repr(C)]
#[derive(Clone, Copy)]
struct Captured {
    buffer: *mut f::pw_buffer,
    // This is a reviewed Rust Option<Buffer> return. Its pointer niche defines
    // None through RAX alone; the RDX payload need not be initialized then.
    // Never turn that payload into a Rust pointer or inspect it on the None path.
    stream: std::mem::MaybeUninit<*mut f::pw_stream>,
}
const _: () = assert!(size_of::<Captured>() == 16 && std::mem::offset_of!(Captured, stream) == 8);
type Dequeue = unsafe extern "C" fn(*mut f::pw_stream) -> Captured;
type Release = unsafe extern "C" fn(*mut Captured);
type Return = unsafe extern "C" fn(*mut f::pw_stream, *mut f::pw_buffer) -> i32;
type StreamError = unsafe extern "C" fn(*mut f::pw_stream, i32, *const libc::c_char, ...) -> i32;
#[derive(Clone, Copy)]
struct Native {
    dequeue: Dequeue,
    release: Release,
    restore: Option<Return>,
    error: Option<StreamError>,
}
static NATIVE: OnceLock<Native> = OnceLock::new();
static INSTALL: Mutex<()> = Mutex::new(());
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
static LATEST: AtomicBool = AtomicBool::new(false);
static PACING: AtomicU64 = AtomicU64::new(0);
struct Counters {
    callbacks: AtomicU64,
    frames: AtomicU64,
    dmabuf: AtomicU64,
    memfd: AtomicU64,
    other: AtomicU64,
    empty: AtomicU64,
    skipped: AtomicU64,
    conservative: AtomicU64,
    return_errors: AtomicU64,
    maximum: AtomicU64,
    known_pts: AtomicU64,
    unknown_pts: AtomicU64,
    metadata: AtomicU64,
    paced: AtomicU64,
    bypass: AtomicU64,
    timeline: AtomicU64,
    invalid_timeline: AtomicU64,
    absent_timeline: AtomicU64,
    histogram: [AtomicU64; HISTOGRAM],
}
impl Counters {
    const fn new() -> Self {
        Self {
            callbacks: AtomicU64::new(0),
            frames: AtomicU64::new(0),
            dmabuf: AtomicU64::new(0),
            memfd: AtomicU64::new(0),
            other: AtomicU64::new(0),
            empty: AtomicU64::new(0),
            skipped: AtomicU64::new(0),
            conservative: AtomicU64::new(0),
            return_errors: AtomicU64::new(0),
            maximum: AtomicU64::new(0),
            known_pts: AtomicU64::new(0),
            unknown_pts: AtomicU64::new(0),
            metadata: AtomicU64::new(0),
            paced: AtomicU64::new(0),
            bypass: AtomicU64::new(0),
            timeline: AtomicU64::new(0),
            invalid_timeline: AtomicU64::new(0),
            absent_timeline: AtomicU64::new(0),
            histogram: [const { AtomicU64::new(0) }; HISTOGRAM],
        }
    }
}
static COUNTS: Counters = Counters::new();
fn increment(value: &AtomicU64) {
    value.fetch_add(1, Ordering::Relaxed);
}
fn now() -> u64 {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: one initialized writable timespec for a monotonic scalar query.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } == 0 {
        (time.tv_sec as u64)
            .saturating_mul(1_000_000_000)
            .saturating_add(time.tv_nsec as u64)
    } else {
        0
    }
}
#[derive(Default)]
struct Local {
    stream: usize,
    generation: u64,
    pacer: Option<Pacer>,
    processing: usize,
    started: u64,
}
thread_local! { static LOCAL: RefCell<Local> = RefCell::new(Local::default()); }
fn configure(fps: u32) {
    let _ = PACING.try_update(Ordering::Relaxed, Ordering::Relaxed, |before| {
        (before & 255 != u64::from(fps))
            .then_some((before.wrapping_add(256) & !255) | u64::from(fps))
    });
}
fn due(stream: usize, generation: u64, time: u64) -> bool {
    LOCAL.with(|local| {
        let mut local = local.borrow_mut();
        if (local.stream, local.generation) != (stream, generation) {
            local.stream = stream;
            local.generation = generation;
            local.pacer = Pacer::new((generation & 255) as u32);
        }
        local.pacer.as_mut().is_none_or(|pacer| pacer.admit(time))
    })
}
struct Buffer<'a>(&'a f::spa_buffer);
impl<'a> Buffer<'a> {
    /// Caller retains the native pw_buffer, its inventories and metadata during
    /// this callback. Checked counts precede every slice; nothing is retained.
    unsafe fn borrowed(value: *const f::pw_buffer) -> Option<Self> {
        // SAFETY: the native callback owns this optional buffer and its descriptor.
        let value = unsafe { value.as_ref()?.buffer.as_ref()? };
        Some(Self(value))
    }
    fn data(&self, maximum: usize) -> Option<&[f::spa_data]> {
        if self.0.n_datas as usize > maximum || self.0.datas.is_null() {
            return None;
        }
        // SAFETY: native callback retains exactly n_datas initialized entries.
        Some(unsafe { std::slice::from_raw_parts(self.0.datas, self.0.n_datas as usize) })
    }
    fn metadata(&self) -> Option<&[f::spa_meta]> {
        if self.0.n_metas > 64 {
            return None;
        }
        if self.0.n_metas == 0 {
            return Some(&[]);
        }
        if self.0.metas.is_null() {
            return None;
        }
        // SAFETY: checked bounded inventory is borrowed from the native callback.
        Some(unsafe { std::slice::from_raw_parts(self.0.metas, self.0.n_metas as usize) })
    }
    fn supersedable(&self) -> bool {
        let (Some(data), Some(metadata)) = (self.data(4), self.metadata()) else {
            return false;
        };
        if data.is_empty() {
            return false;
        }
        for data in data {
            // SAFETY: a native data entry's optional chunk remains borrowed here.
            let Some(chunk) = (unsafe { data.chunk.as_ref() }) else {
                return false;
            };
            if ![f::SPA_DATA_DmaBuf, f::SPA_DATA_MemFd].contains(&data.type_)
                || chunk.size == 0
                || chunk.flags != 0
            {
                return false;
            }
        }
        for meta in metadata {
            if meta.data.is_null() {
                return false;
            }
            match meta.type_ {
                f::SPA_META_Header if meta.size as usize >= size_of::<f::spa_meta_header>() => {
                    // SAFETY: metadata size covers the header; read only its
                    // initialized flags, with no C alignment/padding assumption.
                    if unsafe {
                        ptr::addr_of!((*meta.data.cast::<f::spa_meta_header>()).flags)
                            .read_unaligned()
                    } != 0
                    {
                        return false;
                    }
                }
                f::SPA_META_VideoCrop | f::SPA_META_VideoDamage
                    if meta.size as usize >= size_of::<f::spa_meta_region>() => {}
                f::SPA_META_Busy if meta.size as usize >= size_of::<f::spa_meta_busy>() => {}
                _ => return false,
            }
        }
        true
    }
    fn observe_sync(&self, counts: &Counters) {
        let Some(data) = self.data(64) else {
            return;
        };
        if data
            .first()
            .is_none_or(|data| data.type_ != f::SPA_DATA_DmaBuf)
        {
            return;
        }
        let Some(metadata) = self.metadata() else {
            increment(&counts.invalid_timeline);
            return;
        };
        let Some(timeline) = metadata.iter().find(|meta| meta.type_ == 9) else {
            increment(&counts.absent_timeline);
            return;
        };
        let valid = !timeline.data.is_null()
            && timeline.size >= 24
            && data.len() >= 3
            && data[data.len() - 2..]
                .iter()
                .all(|data| data.type_ == 5 && (0..=i64::from(i32::MAX)).contains(&data.fd));
        increment(if valid {
            &counts.timeline
        } else {
            &counts.invalid_timeline
        });
    }
    fn observe(&self, counts: &Counters) {
        self.observe_sync(counts);
        let metadata = self.metadata();
        if let Some(metadata) = metadata {
            for meta in metadata {
                if meta.type_ < 64 {
                    counts
                        .metadata
                        .fetch_or(1u64 << meta.type_, Ordering::Relaxed);
                }
            }
        }
        let Some(data) = self.data(64).and_then(|data| data.first()) else {
            return;
        };
        increment(match data.type_ {
            f::SPA_DATA_DmaBuf => &counts.dmabuf,
            f::SPA_DATA_MemFd => &counts.memfd,
            _ => &counts.other,
        });
        // SAFETY: the callback retains the optional initialized native chunk.
        let chunk = unsafe { data.chunk.as_ref() };
        if chunk.is_none_or(|chunk| {
            chunk.flags & f::SPA_CHUNK_FLAG_EMPTY as i32 != 0
                || data.type_ != f::SPA_DATA_DmaBuf && chunk.size == 0
        }) {
            increment(&counts.empty);
        }
        let known = metadata
            .and_then(|metadata| {
                metadata.iter().find(|meta| {
                    meta.type_ == f::SPA_META_Header
                        && !meta.data.is_null()
                        && meta.size as usize >= size_of::<f::spa_meta_header>()
                })
            })
            .is_some_and(|meta| {
                // SAFETY: header size was checked; no alignment assumption is made.
                unsafe {
                    ptr::addr_of!((*meta.data.cast::<f::spa_meta_header>()).pts).read_unaligned()
                        >= 0
                }
            });
        increment(if known {
            &counts.known_pts
        } else {
            &counts.unknown_pts
        });
    }
}
unsafe extern "C" fn dequeue(stream: *mut f::pw_stream) -> Captured {
    #[cfg(feature = "capture-delay-fixture")]
    {
        let delay = FIXTURE_DELAY.swap(0, Ordering::Relaxed);
        if delay != 0 {
            std::thread::sleep(std::time::Duration::from_millis(delay));
        }
    }
    // SAFETY: reviewed native callback passes its live stream into this policy.
    unsafe { dequeue_with_clock(stream, now) }
}
unsafe fn dequeue_with_clock(stream: *mut f::pw_stream, clock: impl Fn() -> u64) -> Captured {
    let empty = Captured {
        buffer: ptr::null_mut(),
        stream: std::mem::MaybeUninit::new(stream),
    };
    let Some(native) = NATIVE.get() else {
        return empty;
    };
    increment(&COUNTS.callbacks);
    // SAFETY: original validated native function owns the returned buffer lease.
    let mut chosen = unsafe { (native.dequeue)(stream) };
    if chosen.buffer.is_null() {
        return chosen;
    }
    if LATEST.load(Ordering::Relaxed)
        && let (Some(restore), Some(error)) = (native.restore, native.error)
    {
        let mut buffers = [empty; LIMIT];
        buffers[0] = chosen;
        let mut count = 1;
        // SAFETY: every original dequeue result remains owned until explicit
        // release/restore below; metadata borrows end before those operations.
        let mut safe =
            unsafe { Buffer::borrowed(chosen.buffer) }.is_some_and(|buffer| buffer.supersedable());
        while count < LIMIT {
            // SAFETY: same native stream and original dequeue ownership contract.
            let next = unsafe { (native.dequeue)(stream) };
            if next.buffer.is_null() {
                break;
            }
            buffers[count] = next;
            count += 1;
            // SAFETY: this freshly dequeued lease retains its native metadata.
            safe &= unsafe { Buffer::borrowed(next.buffer) }
                .is_some_and(|buffer| buffer.supersedable());
        }
        COUNTS.maximum.fetch_max(count as u64, Ordering::Relaxed);
        if safe && count < LIMIT {
            for previous in &mut buffers[..count - 1] {
                // SAFETY: only verified supersedable raw frames may be skipped;
                // each lease is returned exactly once through the original API.
                unsafe {
                    (native.release)(previous);
                }
                increment(&COUNTS.skipped);
            }
            chosen = buffers[count - 1];
        } else {
            increment(&COUNTS.conservative);
            for previous in buffers[1..count].iter().rev() {
                // SAFETY: reverse restoration preserves the native front queue
                // and all unfamiliar metadata/fence ownership untouched.
                if unsafe { restore(stream, previous.buffer) } < 0 {
                    increment(&COUNTS.return_errors);
                    LATEST.store(false, Ordering::Relaxed);
                    // SAFETY: native stream error preserves its failed lease;
                    // releasing an unconsumed explicit-sync frame is forbidden.
                    unsafe {
                        error(
                            stream,
                            -libc::EIO,
                            c"DPE capture buffer return failed".as_ptr(),
                        );
                    }
                }
            }
        }
    }
    let config = PACING.load(Ordering::Relaxed);
    if config & 255 != 0 {
        // SAFETY: chosen is still a live native lease and is not retained here.
        if !unsafe { Buffer::borrowed(chosen.buffer) }.is_some_and(|buffer| buffer.supersedable()) {
            increment(&COUNTS.bypass);
        } else if !due(stream as usize, config, clock()) {
            // SAFETY: an explicitly supersedable lease can be paced out once.
            unsafe {
                (native.release)(&mut chosen);
            }
            increment(&COUNTS.paced);
            return empty;
        }
    }
    increment(&COUNTS.frames);
    // SAFETY: same scoped native metadata contract as the dequeue above.
    if let Some(buffer) = unsafe { Buffer::borrowed(chosen.buffer) } {
        buffer.observe(&COUNTS);
    }
    LOCAL.with(|local| {
        let mut local = local.borrow_mut();
        local.processing = chosen.buffer as usize;
        local.started = clock();
    });
    chosen
}
unsafe extern "C" fn release(buffer: *mut Captured) {
    let Some(native) = NATIVE.get() else {
        return;
    };
    if buffer.is_null() {
        return;
    }
    // SAFETY: original native release call supplies its live lease record.
    let value = unsafe { (*buffer).buffer as usize };
    LOCAL.with(|local| {
        let mut local = local.borrow_mut();
        if value == local.processing && value != 0 {
            let us = now().saturating_sub(local.started) / 1000;
            let bucket = (u64::BITS - us.max(1).leading_zeros() - 1) as usize;
            increment(&COUNTS.histogram[bucket.min(HISTOGRAM - 1)]);
            local.processing = 0;
        }
    });
    // SAFETY: the original function receives the same still-owned native lease.
    unsafe {
        (native.release)(buffer);
    }
}

unsafe fn arguments(env: Env, info: Info) -> Result<(CString, [u32; 4], bool), &'static CStr> {
    let api = node::api().ok_or(c"Node API unavailable")?;
    let mut args = [ptr::null_mut(); 6];
    let mut count = args.len();
    let mut filename = [0u8; 4096];
    let mut length = 0;
    let mut latest = false;
    // SAFETY: Node provides this live call; bounded outputs match their capacities.
    unsafe {
        if (api.info)(
            env,
            info,
            &mut count,
            args.as_mut_ptr(),
            ptr::null_mut(),
            ptr::null_mut(),
        ) != 0
            || count != args.len()
            || (api.string)(
                env,
                args[0],
                filename.as_mut_ptr().cast(),
                filename.len(),
                &mut length,
            ) != 0
            || length == 0
            || length >= filename.len() - 1
            || (api.get_bool)(env, args[5], &mut latest) != 0
        {
            return Err(c"Invalid capture adapter arguments.");
        }
        let filename =
            CString::new(&filename[..length]).map_err(|_| c"Invalid capture module filename.")?;
        let mut offsets = [0u32; 4];
        for (value, offset) in args[1..5].iter().zip(&mut offsets) {
            if (api.uint)(env, *value, offset) != 0 || *offset == 0 {
                return Err(c"Invalid capture adapter offset.");
            }
        }
        Ok((filename, offsets, latest))
    }
}
unsafe fn install_inner(env: Env, info: Info) -> Result<(), &'static CStr> {
    // SAFETY: caller forwards the live Node callback without retaining values.
    let (filename, offsets, latest) = unsafe { arguments(env, info) }?;
    let _installation = INSTALL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let module = Module::loaded(&filename)?;
    let sites = [module.address(offsets[0])?, module.address(offsets[2])?];
    let targets = [module.address(offsets[1])?, module.address(offsets[3])?];
    let installed = INSTALLED.load(Ordering::Acquire);
    if installed != 0 {
        return if installed == sites[0] {
            Ok(())
        } else {
            Err(c"Capture module changed.")
        };
    }
    if sites[1] <= sites[0] || sites[1] - sites[0] > 65536 {
        return Err(c"Capture adapter calls are not in one bounded callback.");
    }
    if let Some(native) = NATIVE.get()
        && (native.dequeue as usize, native.release as usize) != (targets[0], targets[1])
    {
        return Err(c"Capture module changed.");
    }
    // Optional PipeWire FIFO-restoration APIs keep older libraries operational.
    // SAFETY: static library name and exact public PipeWire symbol signatures.
    let raw = unsafe {
        libc::dlopen(
            c"libpipewire-0.3.so.0".as_ptr(),
            libc::RTLD_NOW | libc::RTLD_LOCAL,
        )
    };
    let library = (!raw.is_null()).then(|| node::Library(raw));
    let (mut restore, mut error) = (None, None);
    if let Some(library) = &library {
        // SAFETY: retained PipeWire loader reference and fixed public ABI names.
        unsafe {
            let pointer = libc::dlsym(library.0, c"pw_stream_return_buffer".as_ptr());
            if !pointer.is_null() {
                restore = Some(std::mem::transmute::<*mut c_void, Return>(pointer));
            }
            let pointer = libc::dlsym(library.0, c"pw_stream_set_error".as_ptr());
            if !pointer.is_null() {
                error = Some(std::mem::transmute::<*mut c_void, StreamError>(pointer));
            }
        }
    }
    let branches = [
        Branch {
            site: sites[0],
            target: targets[0],
            opcode: 0xff,
            replacement: dequeue as *const () as usize,
        },
        Branch {
            site: sites[1],
            target: targets[1],
            opcode: 0xff,
            replacement: release as *const () as usize,
        },
    ];
    // SAFETY: startup mutex serializes installation before VoiceEngine starts.
    // The group validates both reviewed FF15 calls/slots before writing either;
    // typed originals and library ownership are published before redirection.
    unsafe {
        call_patch::redirect_group(module, &branches, move || {
            // Both original targets and their indirect slots have now passed
            // the complete retained-module transaction's executable checks.
            let native = Native {
                dequeue: std::mem::transmute::<usize, Dequeue>(targets[0]),
                release: std::mem::transmute::<usize, Release>(targets[1]),
                restore,
                error,
            };
            let _ = NATIVE.set(native);
            LATEST.store(
                latest && restore.is_some() && error.is_some(),
                Ordering::Relaxed,
            );
            if let Some(library) = library {
                std::mem::forget(library);
            }
        })
    }?;
    INSTALLED.store(sites[0], Ordering::Release);
    Ok(())
}
unsafe extern "C" fn install(env: Env, info: Info) -> Value {
    // SAFETY: this Node callback supplies live values to bounded argument reads.
    match unsafe { install_inner(env, info) } {
        Ok(()) => undefined(env),
        Err(message) => node::failure(env, message),
    }
}
fn undefined(env: Env) -> Value {
    let mut value = ptr::null_mut();
    if let Some(api) = node::api() {
        // SAFETY: invoked only by a live Node callback on its owning thread.
        unsafe {
            (api.undefined)(env, &mut value);
        }
    }
    value
}
unsafe extern "C" fn rate(env: Env, info: Info) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    let mut value = ptr::null_mut();
    let mut count = 1;
    let mut fps = f64::NAN;
    // SAFETY: live Node call, exactly one bounded argument/output slot.
    let valid = unsafe {
        (api.info)(
            env,
            info,
            &mut count,
            &mut value,
            ptr::null_mut(),
            ptr::null_mut(),
        ) == 0
            && count == 1
            && (api.double)(env, value, &mut fps) == 0
    };
    if !valid || !fps.is_finite() || !(0.0..=240.0).contains(&fps) || fps.fract() != 0.0 {
        return node::failure(env, c"Capture rate must be an integer from 0 through 240.");
    }
    configure(fps as u32);
    undefined(env)
}
unsafe extern "C" fn stats(env: Env, _: Info) -> Value {
    use std::fmt::Write;
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    let mut json = format!(
        "{{\"installed\":{},\"latestEnabled\":{}",
        INSTALLED.load(Ordering::Acquire) != 0,
        LATEST.load(Ordering::Relaxed)
    );
    for (key, counter) in [
        ("callbacks", &COUNTS.callbacks),
        ("frames", &COUNTS.frames),
        ("dmabufFrames", &COUNTS.dmabuf),
        ("memfdFrames", &COUNTS.memfd),
        ("otherFrames", &COUNTS.other),
        ("emptyFrames", &COUNTS.empty),
        ("skippedFrames", &COUNTS.skipped),
        ("conservativeQueues", &COUNTS.conservative),
        ("returnErrors", &COUNTS.return_errors),
        ("maxDequeued", &COUNTS.maximum),
        ("knownPts", &COUNTS.known_pts),
        ("unknownPts", &COUNTS.unknown_pts),
        ("pacedFrames", &COUNTS.paced),
        ("pacingBypassFrames", &COUNTS.bypass),
        ("syncTimelineFrames", &COUNTS.timeline),
        ("syncTimelineInvalidFrames", &COUNTS.invalid_timeline),
        ("noSyncTimelineFrames", &COUNTS.absent_timeline),
    ] {
        let _ = write!(json, ",\"{key}\":{}", counter.load(Ordering::Relaxed));
    }
    let _ = write!(
        json,
        ",\"metadataTypes\":\"0x{:x}\",\"pacingFps\":{},\"processingUsLog2\":[",
        COUNTS.metadata.load(Ordering::Relaxed),
        PACING.load(Ordering::Relaxed) & 255
    );
    for (index, counter) in COUNTS.histogram.iter().enumerate() {
        let _ = write!(
            json,
            "{}{}",
            if index == 0 { "" } else { "," },
            counter.load(Ordering::Relaxed)
        );
    }
    json.push_str("]}");
    let mut value = ptr::null_mut();
    // SAFETY: Node copies the bounded JSON string before this callback returns.
    unsafe {
        (api.new_string)(env, json.as_ptr().cast(), json.len(), &mut value);
    }
    value
}
pub(crate) unsafe extern "C" fn register(env: Env, exports: Value) -> Value {
    // SAFETY: module initialization owns this environment and exports object.
    unsafe {
        node::register_functions(
            env,
            exports,
            &[
                (c"installCapture", install),
                (c"captureStats", stats),
                (c"setCaptureRate", rate),
            ],
        )
    }
}

#[cfg(feature = "capture-delay-fixture")]
static FIXTURE_DELAY: AtomicU64 = AtomicU64::new(0);
#[cfg(feature = "capture-delay-fixture")]
unsafe extern "C" fn set_fixture_delay(env: Env, info: Info) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    let mut value = ptr::null_mut();
    let mut count = 1;
    let mut delay = f64::NAN;
    // SAFETY: live disposable Node call; one bounded initialized argument slot.
    let valid = unsafe {
        (api.info)(
            env,
            info,
            &mut count,
            &mut value,
            ptr::null_mut(),
            ptr::null_mut(),
        ) == 0
            && count == 1
            && (api.double)(env, value, &mut delay) == 0
    };
    if !valid || !delay.is_finite() || !(0.0..=200.0).contains(&delay) || delay.fract() != 0.0 {
        return node::failure(
            env,
            c"Fixture delay must be an integer from 0 through 200 ms",
        );
    }
    FIXTURE_DELAY.store(delay as u64, Ordering::Relaxed);
    undefined(env)
}
#[cfg(feature = "capture-delay-fixture")]
pub(crate) unsafe fn register_fixture(env: Env, exports: Value) -> Value {
    // SAFETY: the fixture owns module initialization; the product has no delay
    // symbol, environment switch or branch when this feature is absent.
    unsafe {
        if register(env, exports).is_null() {
            return ptr::null_mut();
        }
        node::register_functions(env, exports, &[(c"testCaptureDelay", set_fixture_delay)])
    }
}

#[cfg(test)]
#[path = "capture_tests.rs"]
mod tests;
