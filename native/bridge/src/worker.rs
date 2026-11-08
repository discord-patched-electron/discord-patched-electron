// SPDX-License-Identifier: MIT
//! A capture owns its socket, wake descriptor and worker. Opaque handles are
//! monotonic tokens, never pointers to freed allocations. Reentrant stop is safe.
use crate::capture_abi::{CaptureOptions as NativeOptions, FrameHandler as Handler};
use crate::ffi;
use dpe_core::handles::PointerKind;
use dpe_os::{profiler::Span, wire};
use std::{
    cell::Cell,
    collections::HashMap,
    ffi::{CStr, CString, c_void},
    io,
    os::{
        fd::{AsFd, AsRawFd, FromRawFd, OwnedFd},
        unix::fs::MetadataExt,
    },
    ptr,
    sync::{
        Arc, Mutex, MutexGuard, OnceLock,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

const _: () = assert!(size_of::<NativeOptions>() == 40);
const _: () = assert!(std::mem::offset_of!(NativeOptions, config) == 32);
const _: () = assert!(size_of::<ffi::dpe_direct_packet>() == 296);
#[derive(Clone, Copy)]
struct Options {
    fps: u32,
    width: u32,
    height: u32,
    handler: Handler,
    data: usize,
}
impl NativeOptions {
    fn checked(&self) -> Option<Options> {
        if self.fps > 240
            || self.width > 8192
            || self.height > 4320
            || (self.width == 0) != (self.height == 0)
        {
            return None;
        }
        Some(Options {
            fps: self.fps,
            width: self.width,
            height: self.height,
            handler: self.handler?,
            data: self.data as usize,
        })
    }
}
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
struct State {
    socket: OwnedFd,
    wake: OwnedFd,
    options: Mutex<(Options, bool)>,
    stopping: AtomicBool,
}
impl Drop for State {
    fn drop(&mut self) {
        ACTIVE.fetch_sub(1, Ordering::Relaxed);
    }
}
struct Capture {
    state: Arc<State>,
    worker: JoinHandle<()>,
}
static REGISTRY: OnceLock<Mutex<HashMap<usize, Capture>>> = OnceLock::new();
static NEXT: AtomicUsize = AtomicUsize::new(1);
fn registry() -> &'static Mutex<HashMap<usize, Capture>> {
    REGISTRY.get_or_init(Default::default)
}
static ACTIVE: AtomicU64 = AtomicU64::new(0);
static CONVERTED: AtomicU64 = AtomicU64::new(0);
static DROPPED: AtomicU64 = AtomicU64::new(0);
static FAILURES: AtomicU64 = AtomicU64::new(0);
static RECEIVED: AtomicU64 = AtomicU64::new(0);
thread_local! { static DELIVERING: Cell<bool> = const { Cell::new(false) }; }
struct Delivery;
impl Drop for Delivery {
    fn drop(&mut self) {
        DELIVERING.set(false);
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_capture_in_delivery() -> bool {
    DELIVERING.get()
}
/// # Safety
/// `output` is writable for five uint64 values.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_capture_stats(output: *mut u64) {
    if output.is_null() {
        return;
    }
    let values =
        [&ACTIVE, &CONVERTED, &DROPPED, &FAILURES, &RECEIVED].map(|n| n.load(Ordering::Relaxed));
    // SAFETY: caller provides five output elements; source is an initialized array.
    unsafe {
        ptr::copy_nonoverlapping(values.as_ptr(), output, values.len());
    }
}
struct Packet(ffi::dpe_direct_packet);
impl Packet {
    fn new(kind: u32) -> Self {
        Self(ffi::dpe_direct_packet {
            magic: 0x44504544,
            abi: 1,
            size: 296,
            type_: kind,
            ..Default::default()
        })
    }
    fn bytes(&self) -> &[u8] {
        // SAFETY: this integer-only wire record has no implicit padding (asserted layout).
        unsafe { std::slice::from_raw_parts((&raw const self.0).cast(), 296) }
    }
    fn bytes_mut(&mut self) -> &mut [u8] {
        // SAFETY: the record contains only integers; every byte pattern is valid.
        unsafe { std::slice::from_raw_parts_mut((&raw mut self.0).cast(), 296) }
    }
}
impl State {
    fn control(&self, kind: u32, o: Options) -> bool {
        let mut p = Packet::new(kind);
        p.0.fps = o.fps;
        p.0.width = o.width;
        p.0.height = o.height;
        wire::send(self.socket.as_fd(), p.bytes(), &[]).is_ok()
    }
    fn wake(&self) -> io::Result<()> {
        loop {
            let value = 1u64;
            // SAFETY: eventfd consumes exactly one live u64; this State owns the descriptor.
            let result =
                unsafe { libc::write(self.wake.as_raw_fd(), (&raw const value).cast(), 8) };
            if result == 8 {
                return Ok(());
            }
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(libc::EAGAIN) {
                return Ok(());
            }
            if error.raw_os_error() != Some(libc::EINTR) {
                return Err(error);
            }
        }
    }
}
struct Gpu {
    pointer: *mut ffi::dpe_direct_gpu,
    major: u32,
    minor: u32,
}
impl Drop for Gpu {
    fn drop(&mut self) {
        // SAFETY: worker exclusively owns the GPU reference returned by create.
        unsafe {
            ffi::dpe_direct_gpu_destroy(self.pointer);
        }
    }
}
impl Gpu {
    fn create(p: &ffi::dpe_direct_packet) -> Option<Self> {
        if p.device_major == 0 {
            return None;
        }
        for entry in std::fs::read_dir("/dev/dri").ok()?.flatten() {
            if !entry.file_name().as_encoded_bytes().starts_with(b"renderD") {
                continue;
            }
            let path = entry.path();
            let metadata = std::fs::symlink_metadata(&path).ok()?;
            if metadata.mode() & libc::S_IFMT != libc::S_IFCHR
                || libc::major(metadata.rdev()) != p.device_major
                || libc::minor(metadata.rdev()) != p.device_minor
            {
                continue;
            }
            let path = CString::new(path.as_os_str().as_encoded_bytes()).ok()?;
            // SAFETY: C backend borrows the terminated path during create and returns an owned reference.
            let pointer = unsafe { ffi::dpe_direct_gpu_create(path.as_ptr()) };
            if !pointer.is_null() {
                return Some(Self {
                    pointer,
                    major: p.device_major,
                    minor: p.device_minor,
                });
            }
        }
        None
    }
}
struct Frame(*mut c_void);
impl Drop for Frame {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: this guard owns the not-yet-delivered frame returned by convert.
            unsafe {
                ffi::dpe_direct_frame_destroy(self.0);
            }
        }
    }
}
fn poll(fds: &mut [libc::pollfd], timeout: i32) -> io::Result<()> {
    // SAFETY: poll borrows the complete initialized descriptor array for this call.
    if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, timeout) } < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}
fn acquire(state: &State, p: &ffi::dpe_direct_packet, message: &wire::Message) -> bool {
    if p.acquire == 0 {
        return true;
    }
    let Some(fd) = message
        .descriptors
        .get(p.acquire as usize - 1)
        .and_then(Option::as_ref)
    else {
        return false;
    };
    let mut wait = [libc::pollfd {
        fd: fd.as_raw_fd(),
        events: libc::POLLIN,
        revents: 0,
    }];
    let began = Instant::now();
    while !state.stopping.load(Ordering::Acquire) && began.elapsed() < Duration::from_millis(500) {
        if poll(&mut wait, 10).is_err_and(|e| e.raw_os_error() != Some(libc::EINTR)) {
            return false;
        }
        if wait[0].revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
            return false;
        }
        if wait[0].revents & libc::POLLIN != 0 {
            let mut info = ffi::sync_file_info::default();
            let request = libc::_IOWR::<ffi::sync_file_info>(b'>' as u32, 4);
            // SAFETY: documented Linux sync_file query, no fence array requested.
            return unsafe { libc::ioctl(fd.as_raw_fd(), request, &mut info) } == 0
                && info.status > 0;
        }
    }
    false
}
fn run(state: &State) -> Result<(), &'static CStr> {
    if !state.control(1, lock(&state.options).0) {
        return Err(c"direct-start-failed");
    }
    let mut gpu: Option<Gpu> = None;
    let (mut generation, mut sequence) = (0, 0);
    while !state.stopping.load(Ordering::Acquire) {
        let mut wait = [
            libc::pollfd {
                fd: state.socket.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: state.wake.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        if let Err(e) = poll(&mut wait, -1) {
            if e.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            return Err(c"direct-poll-failed");
        }
        if wait[1].revents & libc::POLLIN != 0 {
            let mut value = 0u64;
            // SAFETY: eventfd writes one u64 to this initialized local; state owns it.
            unsafe {
                libc::read(state.wake.as_raw_fd(), (&raw mut value).cast(), 8);
            }
            let (options, update) = {
                let mut o = lock(&state.options);
                (o.0, std::mem::take(&mut o.1))
            };
            if state.stopping.load(Ordering::Acquire) {
                break;
            }
            if update && !state.control(4, options) {
                return Err(c"direct-rate-failed");
            }
        }
        if wait[0].revents & libc::POLLIN == 0 {
            if wait[0].revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
                return Err(c"direct-source-disconnected");
            }
            continue;
        }
        let mut packet = Packet::new(0);
        let message = {
            let _span = Span::new(0);
            wire::receive(state.socket.as_fd(), packet.bytes_mut(), 9)
                .map_err(|_| c"direct-protocol-invalid")?
        };
        let Some(message) = message else {
            continue;
        };
        let p = &packet.0;
        if !wire::header(packet.bytes()) || message.count != p.descriptors as usize {
            return Err(c"direct-protocol-invalid");
        }
        if p.type_ != 2
            || p.generation == 0
            || p.sequence == 0
            || p.timestamp_ns == 0
            || p.generation < generation
            || (p.generation == generation && p.sequence <= sequence)
        {
            return Err(if p.type_ == 5 {
                c"selected-source-ended"
            } else {
                c"direct-frame-invalid"
            });
        }
        (generation, sequence) = (p.generation, p.sequence);
        RECEIVED.fetch_add(1, Ordering::Relaxed);
        if gpu.is_none() {
            let _span = Span::new(1);
            gpu = Gpu::create(p);
        }
        let Some(gpu) = gpu
            .as_ref()
            .filter(|g| g.major == p.device_major && g.minor == p.device_minor)
        else {
            return Err(c"direct-gpu-import-or-fence-failed");
        };
        {
            let _span = Span::new(2);
            if !acquire(state, p, &message) {
                return Err(c"direct-gpu-import-or-fence-failed");
            }
        }
        let options = lock(&state.options).0;
        let mut frame = Frame(ptr::null_mut());
        if options.fps != 0 {
            let (mut width, mut height) = (p.width, p.height);
            if options.width != 0 && width > options.width {
                height = (u64::from(height) * u64::from(options.width) / u64::from(width)) as u32;
                width = options.width;
            }
            if options.height != 0 && height > options.height {
                width = (u64::from(width) * u64::from(options.height) / u64::from(height)) as u32;
                height = options.height;
            }
            let fds: [i32; 9] = std::array::from_fn(|i| {
                message.descriptors[i]
                    .as_ref()
                    .map_or(-1, AsRawFd::as_raw_fd)
            });
            let _span = Span::new(3);
            // SAFETY: GPU is exclusively owned by this worker; descriptors remain borrowed
            // until synchronous conversion completes. Backend checks every image extent.
            frame.0 = unsafe {
                ffi::dpe_direct_gpu_convert(gpu.pointer, p, fds.as_ptr(), width & !1, height & !1)
            };
            if frame.0.is_null() && io::Error::last_os_error().raw_os_error() != Some(libc::EAGAIN)
            {
                return Err(c"direct-gpu-conversion-failed");
            }
        }
        // ACK only after completion: producer cannot reuse storage during a GPU read.
        drop(message);
        let mut release = Packet::new(3);
        release.0.generation = p.generation;
        release.0.sequence = p.sequence;
        {
            let _span = Span::new(4);
            if wire::send(state.socket.as_fd(), release.bytes(), &[]).is_err() {
                return Err(c"direct-release-failed");
            }
        }
        if frame.0.is_null() {
            DROPPED.fetch_add(1, Ordering::Relaxed);
            continue;
        }
        CONVERTED.fetch_add(1, Ordering::Relaxed);
        let options = lock(&state.options).0;
        if state.stopping.load(Ordering::Acquire) {
            break;
        }
        let _span = Span::new(5);
        DELIVERING.set(true);
        let _delivery = Delivery;
        let raw = std::mem::replace(&mut frame.0, ptr::null_mut());
        // SAFETY: reviewed callback consumes exactly one owned C frame. Data is an
        // opaque caller-owned token; callbacks cease before nonreentrant destroy returns.
        unsafe {
            (options.handler)(raw, 0, ptr::null(), options.data as *mut c_void);
        }
    }
    Ok(())
}
fn worker(state: Arc<State>) {
    if std::env::var_os("DPE_CAPTURE_PRIORITY").is_some_and(|value| value == "balanced") {
        let _ = dpe_os::scheduling::balanced_current_thread();
    }
    if let Err(error) = run(&state)
        && !state.stopping.load(Ordering::Acquire)
    {
        FAILURES.fetch_add(1, Ordering::Relaxed);
        eprintln!("DPE direct capture: {}", error.to_string_lossy());
        let options = lock(&state.options).0;
        // SAFETY: reviewed error callback borrows the static error string; no frame ownership.
        unsafe {
            (options.handler)(
                ptr::null_mut(),
                1,
                error.as_ptr(),
                options.data as *mut c_void,
            );
        }
    }
}
/// # Safety
/// Options/config are readable native records; the selected direct socket is
/// transferred here. Output is writable for one opaque capture token.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_capture_new(
    value: *const c_void,
    output: *mut *mut c_void,
) -> i32 {
    if !output.is_null() {
        // SAFETY: caller supplies a writable output slot.
        unsafe {
            output.write(ptr::null_mut());
        }
    }
    if value.is_null() {
        return -libc::EINVAL;
    }
    // SAFETY: caller supplies a live reviewed NativeOptions record.
    let native = unsafe { &*value.cast::<NativeOptions>() };
    if native.config.is_null() {
        return -libc::EINVAL;
    }
    // SAFETY: config points to the reviewed two-field record for the duration of this call.
    let config = unsafe { &*native.config };
    if config.node != 0xfffffffe || config.fd < 0 {
        return -libc::EINVAL;
    }
    // SAFETY: selected direct-mode configuration transfers this unique socket descriptor.
    let socket = unsafe { OwnedFd::from_raw_fd(config.fd) };
    let Some(options) = native.checked() else {
        return -libc::EINVAL;
    };
    let (mut kind, mut size) = (0i32, size_of::<i32>() as libc::socklen_t);
    // SAFETY: socket is owned; getsockopt writes to these exact-size local outputs.
    let valid = unsafe {
        libc::getsockopt(
            socket.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_TYPE,
            (&raw mut kind).cast(),
            &mut size,
        )
    } == 0;
    // SAFETY: readiness only reads the already installed native callback metadata.
    let ready = unsafe { ffi::dpe_direct_capture_ready() };
    if output.is_null()
        || !valid
        || kind != libc::SOCK_SEQPACKET
        || !wire::dpe_direct_peer(socket.as_raw_fd())
        || !ready
    {
        return -libc::EINVAL;
    }
    // SAFETY: eventfd creates a new independently owned wake descriptor.
    let wake = unsafe { libc::eventfd(0, libc::EFD_CLOEXEC | libc::EFD_NONBLOCK) };
    if wake < 0 {
        return -libc::ENOMEM;
    }
    // SAFETY: successful eventfd returned a new unique fd.
    let wake = unsafe { OwnedFd::from_raw_fd(wake) };
    let Ok(token) = NEXT.try_update(Ordering::Relaxed, Ordering::Relaxed, |n| {
        if n > PointerKind::maximum_serial() {
            None
        } else {
            n.checked_add(1)
        }
    }) else {
        return -libc::ENOMEM;
    };
    let Some(token) = PointerKind::Capture.token(token) else {
        return -libc::ENOMEM;
    };
    let mut captures = lock(registry());
    if captures.len() >= 32 {
        return -libc::ENOMEM;
    }
    ACTIVE.fetch_add(1, Ordering::Relaxed);
    let state = Arc::new(State {
        socket,
        wake,
        options: Mutex::new((options, false)),
        stopping: AtomicBool::new(false),
    });
    let owner = state.clone();
    let Ok(worker) = thread::Builder::new()
        .name("DPE capture".into())
        .spawn(move || worker(owner))
    else {
        ACTIVE.fetch_sub(1, Ordering::Relaxed);
        return -libc::ENOMEM;
    };
    captures.insert(token.get(), Capture { state, worker });
    // SAFETY: token is opaque and must never be dereferenced by the native caller.
    unsafe {
        output.write(token.get() as *mut c_void);
    }
    0
}
/// # Safety
/// Value is a readable options record; result is writable when the token is owned.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_capture_update(
    pointer: *mut c_void,
    value: *const c_void,
    result: *mut i32,
) -> bool {
    let captures = lock(registry());
    let Some(capture) = captures.get(&(pointer as usize)) else {
        return false;
    };
    if result.is_null() {
        return true;
    }
    let options = if value.is_null() {
        None
    } else {
        // SAFETY: caller borrows the reviewed NativeOptions record through this call.
        unsafe { &*value.cast::<NativeOptions>() }.checked()
    };
    let status = if let Some(options) =
        options.filter(|_| !capture.state.stopping.load(Ordering::Acquire))
    {
        *lock(&capture.state.options) = (options, true);
        if capture.state.wake().is_ok() {
            0
        } else {
            -libc::EIO
        }
    } else {
        -libc::EINVAL
    };
    // SAFETY: owned-token branch requires this writable result slot.
    unsafe {
        result.write(status);
    }
    true
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_capture_renditions(pointer: *mut c_void) -> bool {
    lock(registry()).contains_key(&(pointer as usize))
}
/// # Safety
/// Result is writable when the opaque token is still owned by this implementation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_capture_destroy(
    pointer: *mut c_void,
    result: *mut i32,
) -> bool {
    let capture = lock(registry()).remove(&(pointer as usize));
    let Some(capture) = capture else {
        return false;
    };
    capture.state.stopping.store(true, Ordering::Release);
    let _ = capture.state.wake();
    if capture.worker.thread().id() != thread::current().id() {
        let _ = capture.worker.join();
    }
    // Self-destruction drops the JoinHandle; the worker's Arc retains all state
    // through callback return and GPU teardown, without a special free branch.
    if !result.is_null() {
        // SAFETY: caller provides this writable status output.
        unsafe {
            result.write(0);
        }
    }
    true
}
