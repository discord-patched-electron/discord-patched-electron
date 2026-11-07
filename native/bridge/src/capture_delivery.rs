// SPDX-License-Identifier: MIT
//! Native callback leases and publication wakes. Tokens retain no C allocation
//! address; weak thread bindings cannot outlive a capture's registry owner.
use crate::capture_abi::{CaptureOptions, FrameHandler};
use dpe_core::{delivery::Delivery, handles::Handles};
use std::{
    cell::RefCell,
    ffi::{c_char, c_void},
    io::Write as _,
    mem::MaybeUninit,
    ptr,
    sync::{
        Arc, Condvar, Mutex, MutexGuard, OnceLock, Weak,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
type ClonePlanar = unsafe extern "C" fn(
    *mut c_void,
    *mut *const u8,
    *mut usize,
    *mut usize,
    *mut usize,
    *mut usize,
    *mut usize,
) -> *mut c_void;
type Release = unsafe extern "C" fn(*mut c_void);
type Update = unsafe extern "C" fn(*mut c_void, *const CaptureOptions) -> i32;
type Read = unsafe extern "C" fn(*mut c_void) -> *mut c_void;
type Write = unsafe extern "C" fn(*mut c_void, *mut c_void);
// The scoped native Rust receiver uses caller-owned Result storage. Do not
// interpret RAX: its payload is irrelevant to the reviewed sret call contract.
type Receive = unsafe extern "C" fn(*mut c_void, *mut c_void, u64, u32) -> MaybeUninit<*mut c_void>;
type Overdue = unsafe extern "C" fn(*mut c_void) -> bool;
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Functions {
    pub clone: Option<ClonePlanar>,
    pub release_planar: Option<Release>,
    pub release_frame: Option<Release>,
}
#[derive(Clone, Copy)]
struct Native {
    clone: ClonePlanar,
    release_planar: Release,
    release_frame: Release,
}
static NATIVE: OnceLock<Native> = OnceLock::new();
#[derive(Clone, Copy)]
struct NativeWake {
    read: Read,
    write: Write,
    receive: Receive,
    overdue: Overdue,
}
static ORIGINAL_WAKE: OnceLock<NativeWake> = OnceLock::new();
static WAKE_ACTIVE: AtomicBool = AtomicBool::new(false);
static PROBES: OnceLock<Handles<Arc<Probe>>> = OnceLock::new();
fn probes() -> &'static Handles<Arc<Probe>> {
    PROBES.get_or_init(|| Handles::new(32))
}
fn probe(token: *mut c_void) -> Option<Arc<Probe>> {
    probes().get(token as usize)
}
fn lock<T>(value: &Mutex<T>) -> MutexGuard<'_, T> {
    value
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
static WAKERS: OnceLock<Mutex<[Weak<Probe>; 32]>> = OnceLock::new();
fn wakers() -> &'static Mutex<[Weak<Probe>; 32]> {
    WAKERS.get_or_init(|| Mutex::new(std::array::from_fn(|_| Weak::new())))
}
struct Lease {
    value: usize,
    release: Release,
}
impl Drop for Lease {
    fn drop(&mut self) {
        // SAFETY: the reviewed clone export grants one staging lease. Its
        // destroy export supports cross-thread retirement (the original ABI
        // also retires it after native capture joins on the UI thread).
        unsafe {
            (self.release)(self.value as *mut c_void);
        }
    }
}
struct Data {
    policy: Delivery,
    handler: FrameHandler,
    data: usize,
    lease: Option<Lease>,
    auto_fast: bool,
    log: bool,
    last_report: u64,
}
struct Wake {
    serial: u64,
    frames: u64,
    events: bool,
    stopping: bool,
}
struct Probe {
    id: u32,
    data: Mutex<Data>,
    wake: Mutex<Wake>,
    changed: Condvar,
    framebuffer: AtomicUsize,
}
#[repr(C)]
pub struct Snapshot {
    id: u64,
    auto_fast: u64,
    fast: u64,
    log: u64,
    requested_rate: u64,
    events: u64,
    stopping: u64,
    wake_serial: u64,
    frame_serial: u64,
    callbacks: u64,
    fresh: u64,
    duplicate: u64,
    unknown: u64,
    forwarded: u64,
    dropped: u64,
    heartbeats: u64,
    lease: *mut c_void,
    identity: *mut c_void,
    framebuffer: *mut c_void,
    next_unknown_ns: u64,
    data: *mut c_void,
    last_forward_ns: u64,
}
/// # Safety
/// Caller supplies one writable diagnostic result record.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_inspect(token: *mut c_void, output: *mut Snapshot) -> bool {
    let Some(owner) = probe(token) else {
        return false;
    };
    if output.is_null() {
        return false;
    }
    let data = lock(&owner.data);
    let state = lock(&owner.wake);
    let c = data.policy.counts;
    let (identity, last_forward_ns, next_unknown_ns) = data.policy.tracking();
    let snapshot = Snapshot {
        id: u64::from(owner.id),
        auto_fast: u64::from(data.auto_fast),
        fast: u64::from(data.policy.fast()),
        log: u64::from(data.log),
        requested_rate: u64::from(data.policy.rate()),
        events: u64::from(state.events),
        stopping: u64::from(state.stopping),
        wake_serial: state.serial,
        frame_serial: state.frames,
        callbacks: c.callbacks,
        fresh: c.fresh,
        duplicate: c.duplicate,
        unknown: c.unknown,
        forwarded: c.forwarded,
        dropped: c.dropped,
        heartbeats: c.heartbeats,
        lease: data
            .lease
            .as_ref()
            .map_or(ptr::null_mut(), |l| l.value as *mut c_void),
        identity: identity.unwrap_or(0) as *mut c_void,
        framebuffer: owner.framebuffer.load(Ordering::Acquire) as *mut c_void,
        next_unknown_ns,
        data: data.data as *mut c_void,
        last_forward_ns,
    };
    // SAFETY: write the entire output without borrowing uninitialized C storage.
    unsafe {
        output.write(snapshot);
    }
    true
}
#[derive(Default)]
struct Local {
    probe: Weak<Probe>,
    framebuffer: usize,
    serial: u64,
    frames: u64,
    deadline: Option<Instant>,
    force: bool,
}
thread_local! {static LOCAL:RefCell<Local>=RefCell::new(Local::default());}
fn enabled(name: &str) -> bool {
    std::env::var_os(name).is_some_and(|value| value == "1")
}
fn fast(auto: bool) -> bool {
    std::env::var_os("DPE_CAPTURE_DELIVERY_FAST")
        .map_or(auto && !enabled("DPE_CAPTURE_DELIVERY_PROBE"), |value| {
            value == "1"
        })
}
fn events(fast: bool) -> bool {
    WAKE_ACTIVE.load(Ordering::Acquire)
        && fast
        && std::env::var_os("DPE_CAPTURE_DELIVERY_EVENT").is_none_or(|v| v != "0")
}
fn now() -> u64 {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: writable initialized output for a fixed monotonic scalar query.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } == 0 {
        (time.tv_sec as u64)
            .saturating_mul(1_000_000_000)
            .saturating_add(time.tv_nsec as u64)
    } else {
        0
    }
}
fn report(owner: &Probe, data: &Data) {
    if !data.log {
        return;
    }
    let c = data.policy.counts;
    let _ = writeln!(
        std::io::stderr().lock(),
        "[DPE capture delivery] {{\"requestId\":{},\"fast\":{},\"eventDriven\":{},\"requestedFps\":{},\"callbacks\":{},\"fresh\":{},\"duplicate\":{},\"unknown\":{},\"forwarded\":{},\"dropped\":{},\"heartbeats\":{}}}",
        owner.id,
        data.policy.fast(),
        lock(&owner.wake).events,
        data.policy.rate(),
        c.callbacks,
        c.fresh,
        c.duplicate,
        c.unknown,
        c.forwarded,
        c.dropped,
        c.heartbeats
    );
}
fn wake(owner: &Probe, stop: bool) {
    let mut state = lock(&owner.wake);
    state.stopping |= stop;
    state.serial = state.serial.wrapping_add(1);
    owner.changed.notify_one();
}
fn bind(owner: &Arc<Probe>) {
    if !WAKE_ACTIVE.load(Ordering::Acquire) {
        return;
    }
    LOCAL.with(|local| {
        let mut local = local.borrow_mut();
        if local.framebuffer == 0 {
            return;
        }
        if !Weak::ptr_eq(&local.probe, &Arc::downgrade(owner))
            || owner.framebuffer.load(Ordering::Acquire) != local.framebuffer
        {
            owner
                .framebuffer
                .store(local.framebuffer, Ordering::Release);
            let mut state = lock(&owner.wake);
            local.probe = Arc::downgrade(owner);
            local.serial = state.serial;
            state.serial = state.serial.wrapping_add(1);
            local.frames = state.frames;
            state.frames = state.frames.wrapping_add(1);
            local.deadline = Some(Instant::now() + Duration::from_millis(500));
        }
    });
}
/// # Safety
/// All supplied reviewed exports stay loaded for process life. Capture options
/// are borrowed only here; callback/data remain valid through native destroy.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_create(
    id: u32,
    options: *const CaptureOptions,
    auto_fast: bool,
    functions: Functions,
) -> *mut c_void {
    // SAFETY: caller supplies a complete initialized optional ABI record.
    let Some(options) = (unsafe { options.as_ref() }) else {
        return ptr::null_mut();
    };
    let (Some(handler), Some(clone), Some(release_planar), Some(release_frame)) = (
        options.handler,
        functions.clone,
        functions.release_planar,
        functions.release_frame,
    ) else {
        return ptr::null_mut();
    };
    let use_fast = fast(auto_fast);
    if !use_fast && !enabled("DPE_CAPTURE_DELIVERY_PROBE") {
        return ptr::null_mut();
    }
    let old = NATIVE.get_or_init(|| Native {
        clone,
        release_planar,
        release_frame,
    });
    if (
        old.clone as usize,
        old.release_planar as usize,
        old.release_frame as usize,
    ) != (
        clone as usize,
        release_planar as usize,
        release_frame as usize,
    ) {
        return ptr::null_mut();
    }
    let policy = Delivery::new(options.fps, use_fast);
    let owner = Arc::new(Probe {
        id,
        data: Mutex::new(Data {
            policy,
            handler,
            data: options.data as usize,
            lease: None,
            auto_fast,
            log: enabled("DPE_CAPTURE_DELIVERY_FAST") || enabled("DPE_CAPTURE_DELIVERY_PROBE"),
            last_report: 0,
        }),
        wake: Mutex::new(Wake {
            serial: 0,
            frames: 0,
            events: events(use_fast && options.fps > 0 && options.fps < 240),
            stopping: false,
        }),
        changed: Condvar::new(),
        framebuffer: AtomicUsize::new(0),
    });
    let mut wakers = lock(wakers());
    let Some(slot) = wakers.iter_mut().find(|w| w.strong_count() == 0) else {
        return ptr::null_mut();
    };
    let Ok(token) = probes().insert(owner.clone()) else {
        return ptr::null_mut();
    };
    *slot = Arc::downgrade(&owner);
    token.get() as *mut c_void
}
/// # Safety
/// Caller provides borrowed options and one writable adapted record. No pointer
/// to those stack records survives the synchronous native new/update call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_adapt(
    token: *mut c_void,
    options: *const CaptureOptions,
    adapted: *mut CaptureOptions,
) -> bool {
    let Some(owner) = probe(token) else {
        return false;
    };
    // SAFETY: complete optional borrowed native input supplied by caller.
    let Some(options) = (unsafe { options.as_ref() }) else {
        return false;
    };
    if adapted.is_null() {
        return false;
    }
    let mut value = *options;
    value.handler = Some(dpe_delivery_frame);
    value.data = token;
    if lock(&owner.data).policy.fast() {
        value.fps = 240;
    }
    // SAFETY: caller grants a writable full record; output may be uninitialized.
    unsafe {
        adapted.write(value);
    }
    true
}
/// # Safety
/// The reviewed update export only copies/enqueues options; it cannot call a
/// frame callback inline or join that callback's thread. This allows its config
/// lock to preserve atomic update/failure behavior across the synchronous call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_update(
    token: *mut c_void,
    capture: *mut c_void,
    options: *const CaptureOptions,
    update: Option<Update>,
) -> i32 {
    let (Some(owner), Some(update)) = (probe(token), update) else {
        return -libc::EINVAL;
    };
    // SAFETY: caller supplies live complete options through native enqueue.
    let Some(options) = (unsafe { options.as_ref() }) else {
        return -libc::EINVAL;
    };
    let mut data = lock(&owner.data);
    let use_fast = fast(data.auto_fast) && options.fps > 0 && options.fps < 240;
    let mut adapted = *options;
    adapted.handler = Some(dpe_delivery_frame);
    adapted.data = token;
    if use_fast {
        adapted.fps = 240;
    }
    // SAFETY: retained typed native export, live capture and stack options;
    // reviewed enqueue contract excludes inline callback reentry/deadlock.
    let result = unsafe { update(capture, &adapted) };
    if result == 0 {
        data.policy.configure(options.fps, use_fast);
        data.log = enabled("DPE_CAPTURE_DELIVERY_FAST") || enabled("DPE_CAPTURE_DELIVERY_PROBE");
        if let Some(handler) = options.handler {
            data.handler = handler;
            data.data = options.data as usize;
        }
        lock(&owner.wake).events = events(use_fast);
        wake(&owner, false);
    }
    result
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_delivery_wake(token: *mut c_void, stop: bool) {
    if let Some(owner) = probe(token) {
        wake(&owner, stop);
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_delivery_destroy(token: *mut c_void) {
    let Some(owner) = probes().remove(token as usize) else {
        return;
    };
    wake(&owner, true);
    owner.framebuffer.store(0, Ordering::Release);
    let mut data = lock(&owner.data);
    report(&owner, &data);
    let lease = data.lease.take();
    drop(data);
    drop(lease);
}
/// # Safety
/// Native callback transfers one optional frame to the handler/destroy export.
/// Its error string remains borrowed during this synchronous call only.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_frame_at(
    frame: *mut c_void,
    status: u32,
    error: *const c_char,
    token: *mut c_void,
    time: u64,
) {
    let Some(native) = NATIVE.get() else {
        return;
    };
    let Some(owner) = probe(token) else {
        if !frame.is_null() {
            // SAFETY: a late callback's owned native frame is retired once.
            unsafe {
                (native.release_frame)(frame);
            }
        }
        return;
    };
    bind(&owner);
    let mut identity = ptr::null();
    let mut size = 0;
    let mut y = 0;
    let mut uv = 0;
    let mut u = 0;
    let mut v = 0;
    let lease = if !frame.is_null() && status == 0 {
        // SAFETY: reviewed clone returns an opaque retained staging lease and
        // scalar layout outputs. No frame pixels or foreign Rust layout are read.
        let raw = unsafe {
            (native.clone)(
                frame,
                &mut identity,
                &mut size,
                &mut y,
                &mut uv,
                &mut u,
                &mut v,
            )
        };
        (!raw.is_null()).then(|| Lease {
            value: raw as usize,
            release: native.release_planar,
        })
    } else {
        None
    };
    let mut data = lock(&owner.data);
    let mut previous = None;
    let mut unclaimed = lease;
    let forward = if !frame.is_null() && status == 0 {
        let known = unclaimed.is_some() && !identity.is_null() && size > 0;
        if known {
            previous = std::mem::replace(&mut data.lease, unclaimed.take());
        }
        let forward = data
            .policy
            .observe(known.then_some(identity as usize), time);
        if data.log
            && time != 0
            && (data.last_report == 0 || time.saturating_sub(data.last_report) >= 2_000_000_000)
        {
            report(&owner, &data);
            data.last_report = time;
        }
        forward
    } else {
        true
    };
    let (handler, opaque) = (data.handler, data.data);
    drop(data);
    drop(previous);
    drop(unclaimed);
    // SAFETY: handler/data stay valid through the native capture's joined
    // destruction. Every lease/model lock is released before consumer reentry.
    unsafe {
        if forward {
            handler(frame, status, error, opaque as *mut c_void);
        } else {
            (native.release_frame)(frame);
        }
    }
}
/// # Safety
/// Same transferred frame/error lifetime as dpe_delivery_frame_at.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_frame(
    frame: *mut c_void,
    status: u32,
    error: *const c_char,
    token: *mut c_void,
) {
    // SAFETY: identical native callback ownership, with a monotonic scalar stamp.
    unsafe {
        dpe_delivery_frame_at(frame, status, error, token, now());
    }
}
/// # Safety
/// Typed reviewed wake exports stay loaded and are published before redirects.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_configure_wake(
    read: Option<Read>,
    write: Option<Write>,
    receive: Option<Receive>,
    overdue: Option<Overdue>,
) -> bool {
    let (Some(read), Some(write), Some(receive), Some(overdue)) = (read, write, receive, overdue)
    else {
        return false;
    };
    if let Some(old) = ORIGINAL_WAKE.get() {
        return (
            old.read as usize,
            old.write as usize,
            old.receive as usize,
            old.overdue as usize,
        ) == (
            read as usize,
            write as usize,
            receive as usize,
            overdue as usize,
        );
    }
    ORIGINAL_WAKE
        .set(NativeWake {
            read,
            write,
            receive,
            overdue,
        })
        .is_ok()
}
/// Activate only after all reviewed call-site redirects commit successfully.
#[unsafe(no_mangle)]
pub extern "C" fn dpe_delivery_enable_wake() -> bool {
    if ORIGINAL_WAKE.get().is_none() {
        return false;
    }
    WAKE_ACTIVE.store(true, Ordering::Release);
    true
}
/// # Safety
/// Native framebuffer and result ownership stay entirely with the original.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_read(buffer: *mut c_void) -> *mut c_void {
    let Some(native) = ORIGINAL_WAKE.get() else {
        return ptr::null_mut();
    };
    LOCAL.with(|local| local.borrow_mut().framebuffer = buffer as usize);
    // SAFETY: retained typed original receives unchanged borrowed native buffer.
    unsafe { (native.read)(buffer) }
}
/// # Safety
/// Original native writer consumes its frame before publishing the wake.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_write(buffer: *mut c_void, frame: *mut c_void) {
    let Some(native) = ORIGINAL_WAKE.get() else {
        return;
    };
    // SAFETY: unchanged typed original publishes the native frame fully first.
    unsafe {
        (native.write)(buffer, frame);
    }
    if buffer.is_null() {
        return;
    }
    let mut matches: [Option<Arc<Probe>>; 32] = std::array::from_fn(|_| None);
    {
        let wakers = lock(wakers());
        for (slot, owner) in matches.iter_mut().zip(wakers.iter()) {
            if let Some(owner) = owner
                .upgrade()
                .filter(|p| p.framebuffer.load(Ordering::Acquire) == buffer as usize)
            {
                *slot = Some(owner);
            }
        }
    }
    for owner in matches.into_iter().flatten() {
        let mut state = lock(&owner.wake);
        if state.events && !state.stopping {
            state.frames = state.frames.wrapping_add(1);
            state.serial = state.serial.wrapping_add(1);
            owner.changed.notify_one();
        }
    }
}
unsafe fn timed_out(result: *const c_void) -> bool {
    if result.is_null() {
        return false;
    }
    // SAFETY: the scoped native receiver initialized its reviewed Result output;
    // inspect only its 9-byte timeout discriminator, never an owned payload.
    unsafe {
        result.cast::<u64>().read_unaligned() == u64::MAX && result.cast::<u8>().add(8).read() == 0
    }
}
/// # Safety
/// The reviewed receiver retains its own Result storage and opaque receiver.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_receive(
    result: *mut c_void,
    receiver: *mut c_void,
    seconds: u64,
    nanos: u32,
) -> *mut c_void {
    let Some(native) = ORIGINAL_WAKE.get() else {
        return result;
    };
    let (owner, framebuffer, serial, frames, deadline) = LOCAL.with(|local| {
        let mut l = local.borrow_mut();
        l.force = false;
        (
            l.probe.upgrade(),
            l.framebuffer,
            l.serial,
            l.frames,
            l.deadline,
        )
    });
    let pass = || {
        // SAFETY: unchanged original timeout and borrowed native storage.
        unsafe {
            (native.receive)(result, receiver, seconds, nanos);
        }
        result
    };
    let Some(owner) = owner else {
        return pass();
    };
    let bypass = {
        let state = lock(&owner.wake);
        !state.events || state.stopping || owner.framebuffer.load(Ordering::Acquire) != framebuffer
    };
    if bypass {
        return pass();
    }
    // SAFETY: a timeout owns no payload. Poll native stop/config before waiting.
    unsafe {
        (native.receive)(result, receiver, 0, 0);
        if !timed_out(result) {
            return result;
        }
    }
    let deadline = deadline.unwrap_or_else(|| Instant::now() + Duration::from_millis(500));
    let mut state = lock(&owner.wake);
    let mut expired = false;
    while state.events && !state.stopping && state.serial == serial {
        let Some(left) = deadline.checked_duration_since(Instant::now()) else {
            expired = true;
            break;
        };
        let (next, timeout) = owner
            .changed
            .wait_timeout(state, left)
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        state = next;
        if timeout.timed_out() {
            expired = true;
            break;
        }
    }
    let (enabled, stopping, next_serial, next_frames) =
        (state.events, state.stopping, state.serial, state.frames);
    drop(state);
    if stopping || !enabled {
        return pass();
    }
    // SAFETY: poll again so a concurrent native message wins over frame wake.
    unsafe {
        (native.receive)(result, receiver, 0, 0);
        if timed_out(result) {
            LOCAL.with(|local| {
                let mut local = local.borrow_mut();
                local.force = next_frames != frames || expired;
                local.serial = next_serial;
                local.frames = next_frames;
                if local.force {
                    local.deadline = Some(Instant::now() + Duration::from_millis(500));
                }
            });
        }
    }
    result
}
/// # Safety
/// Scoped native FPS tracker stays borrowed by its original owner.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_delivery_overdue(tracker: *mut c_void) -> bool {
    if LOCAL.with(|local| std::mem::take(&mut local.borrow_mut().force)) {
        return true;
    }
    // SAFETY: unchanged scalar accessor on its live native tracker.
    ORIGINAL_WAKE
        .get()
        .is_some_and(|native| unsafe { (native.overdue)(tracker) })
}
