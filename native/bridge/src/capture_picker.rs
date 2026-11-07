// SPDX-License-Identifier: MIT
//! Picker requests own selected sockets until the reviewed native constructor
//! takes them. Cancellation leaves a descriptor/identity tombstone; native
//! callbacks and constructors always run after releasing the request lock.
use crate::{
    capture_abi::{CaptureOptions, Configuration, PickerCallback, PickerOptions, PickerResult},
    capture_delivery::{self as delivery, Functions},
};
use dpe_core::handles::pointer_reserved;
use std::{
    ffi::{CStr, c_char, c_void},
    os::fd::{AsRawFd, IntoRawFd, OwnedFd},
    ptr,
    sync::{Mutex, MutexGuard, OnceLock},
};
type Show = unsafe extern "C" fn(*const PickerOptions) -> i32;
type New = unsafe extern "C" fn(*const CaptureOptions, *mut *mut c_void) -> i32;
type Destroy = unsafe extern "C" fn(*mut c_void) -> i32;
type Update = unsafe extern "C" fn(*mut c_void, *const CaptureOptions) -> i32;
type Notify = unsafe extern "C" fn(*const c_char, u32, u32) -> bool;
/// Startup-only linkage. Every function and selected-storage address is checked
/// against the retained native module before this record is published.
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct Native {
    pub show: Show,
    pub new: New,
    pub destroy: Destroy,
    pub update: Update,
    pub frames: Functions,
    pub selected_mutex: usize,
    pub selected_config: usize,
    pub selected_present: usize,
    pub notify: Notify,
}
static NATIVE: OnceLock<Native> = OnceLock::new();
const _: () = assert!(size_of::<libc::pthread_mutex_t>() == 40);
#[repr(u32)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum Phase {
    Waiting = 1,
    Selected,
    Starting,
    Active,
    Cancelled,
}
#[derive(Clone, Copy)]
struct Options {
    source: u32,
    padding: u32,
    callback: Option<PickerCallback>,
    data: usize,
}
impl From<PickerOptions> for Options {
    fn from(value: PickerOptions) -> Self {
        Self {
            source: value.source,
            padding: value.padding,
            callback: value.callback,
            data: value.data as usize,
        }
    }
}
impl Options {
    fn native(self) -> PickerOptions {
        PickerOptions {
            source: self.source,
            padding: self.padding,
            callback: self.callback,
            data: self.data as *const c_void,
        }
    }
}
#[derive(Clone, Copy)]
struct Identity {
    fd: i32,
    node: u32,
    device: u64,
    inode: u64,
}
impl Identity {
    fn checked(fd: i32, node: u32) -> Option<Self> {
        // SAFETY: fstat initializes this complete system record on success.
        let mut stat = unsafe { std::mem::zeroed::<libc::stat>() };
        // SAFETY: only an integer descriptor and writable local record are passed.
        (unsafe { libc::fstat(fd, &mut stat) } == 0).then_some(Self {
            fd,
            node,
            device: stat.st_dev,
            inode: stat.st_ino,
        })
    }
    fn matches(self) -> bool {
        Self::checked(self.fd, self.node)
            .is_some_and(|value| value.device == self.device && value.inode == self.inode)
    }
}
struct Request {
    phase: Phase,
    id: u32,
    options: Options,
    selected: Option<Identity>,
    direct: bool,
    delivery_only: bool,
    capture: usize,
    delivery: usize,
}
struct State {
    requests: [Option<Request>; 32],
    next: u32,
    current: u32,
    enabled: bool,
}
static STATE: Mutex<State> = Mutex::new(State {
    requests: [const { None }; 32],
    next: 0,
    current: 0,
    enabled: false,
});
fn lock() -> MutexGuard<'static, State> {
    STATE
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
impl State {
    fn next_id(&mut self) -> u32 {
        loop {
            self.next = self.next.wrapping_add(1);
            if self.next != 0 && self.index(self.next).is_none() {
                return self.next;
            }
        }
    }
    fn index(&self, id: u32) -> Option<usize> {
        self.requests
            .iter()
            .position(|request| request.as_ref().is_some_and(|request| request.id == id))
    }
    fn notify(&self, event: &CStr, request: &Request) -> bool {
        if !self.enabled || request.delivery_only {
            return false;
        }
        let Some(native) = NATIVE.get() else {
            return false;
        };
        // SAFETY: the startup-published dispatcher only enqueues a bounded TSFN
        // item; it cannot execute JS or reenter the picker while this lock is held.
        unsafe { (native.notify)(event.as_ptr(), request.id, request.options.source) }
    }
    fn cancel(&mut self, index: usize) {
        let request = self.requests[index].as_ref().expect("occupied request");
        if request.phase == Phase::Selected
            && let Some(selected) = request.selected
            && let Some(native) = NATIVE.get()
        {
            // SAFETY: installation validates these exact revision-specific
            // writable objects, including the pthread mutex ABI. The native
            // selected-config accessors serialize on this same mutex.
            unsafe {
                let mutex = native.selected_mutex as *mut libc::pthread_mutex_t;
                if libc::pthread_mutex_lock(mutex) == 0 {
                    let config = native.selected_config as *const Configuration;
                    let present = native.selected_present as *mut bool;
                    if *present && (*config).fd == selected.fd && (*config).node == selected.node {
                        present.write(false);
                    }
                    libc::pthread_mutex_unlock(mutex);
                }
            }
            if selected.matches() {
                // SAFETY: this selected request still owns the matching socket;
                // Starting transfers it before invoking the native constructor.
                unsafe {
                    libc::close(selected.fd);
                }
            }
        }
        self.requests[index]
            .as_mut()
            .expect("occupied request")
            .phase = Phase::Cancelled;
        self.notify(
            c"cancelled",
            self.requests[index].as_ref().expect("occupied request"),
        );
    }
}
/// # Safety
/// Startup supplies validated process-lifetime native linkage, before any hook.
#[unsafe(no_mangle)]
pub(crate) unsafe extern "C" fn dpe_picker_configure(native: Native) -> bool {
    NATIVE.set(native).is_ok()
}
/// Dispatch may be enabled only while its nonblocking callback is live.
#[unsafe(no_mangle)]
pub extern "C" fn dpe_picker_enable(enabled: bool) {
    lock().enabled = enabled;
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_picker_idle() -> bool {
    lock().requests.iter().all(Option::is_none)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_picker_current() -> u32 {
    lock().current
}
#[repr(C)]
pub struct Snapshot {
    phase: u32,
    fd: i32,
    delivery: *mut c_void,
    delivery_only: bool,
}
/// Read-only diagnostics, with no exposure of the registry representation.
/// # Safety
/// Output points to one writable Snapshot.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_picker_inspect(id: u32, output: *mut Snapshot) -> bool {
    if output.is_null() {
        return false;
    }
    let state = lock();
    let request = if id == 0 {
        state.requests.iter().flatten().next()
    } else {
        state
            .index(id)
            .and_then(|index| state.requests[index].as_ref())
    };
    let Some(request) = request else {
        return false;
    };
    // SAFETY: caller provides one complete writable diagnostic record.
    unsafe {
        output.write(Snapshot {
            phase: request.phase as u32,
            fd: request.selected.map_or(-1, |value| value.fd),
            delivery: request.delivery as *mut c_void,
            delivery_only: request.delivery_only,
        });
    }
    true
}
/// Retire pending selections; active native callbacks remain owned until join.
#[unsafe(no_mangle)]
pub extern "C" fn dpe_picker_cleanup() {
    let mut state = lock();
    state.enabled = false;
    for index in 0..state.requests.len() {
        match state.requests[index].as_ref().map(|request| request.phase) {
            Some(Phase::Selected) => state.cancel(index),
            Some(Phase::Waiting) => state.requests[index] = None,
            _ => (),
        }
    }
}
/// Preserve the core -> dispatcher lock order used by notify. A retired Node
/// environment cannot disable a new dispatcher after its replacement is live.
pub(super) fn change_dispatch(change: impl FnOnce() -> Option<bool>) {
    let mut state = lock();
    let Some(enabled) = change() else {
        return;
    };
    state.enabled = false;
    for index in 0..state.requests.len() {
        match state.requests[index].as_ref().map(|r| r.phase) {
            Some(Phase::Selected) => state.cancel(index),
            Some(Phase::Waiting) => state.requests[index] = None,
            _ => (),
        }
    }
    state.enabled = enabled;
}
/// # Safety
/// Options is null or a readable reviewed C record, borrowed through this call.
#[unsafe(no_mangle)]
pub(crate) unsafe extern "C" fn dpe_picker_show(options: *const PickerOptions) -> i32 {
    let Some(native) = NATIVE.get() else {
        return -libc::EINVAL;
    };
    let sent = if !options.is_null()
        // SAFETY: caller borrows the reviewed record for this invocation.
        && unsafe { (*options).callback.is_some() }
    {
        let mut state = lock();
        if state.enabled {
            for index in 0..state.requests.len() {
                match state.requests[index].as_ref().map(|request| request.phase) {
                    Some(Phase::Waiting | Phase::Selected) => {
                        state.cancel(index);
                        state.requests[index] = None;
                    }
                    Some(Phase::Cancelled) => state.requests[index] = None,
                    _ => (),
                }
            }
            if let Some(index) = state.requests.iter().position(Option::is_none) {
                let id = state.next_id();
                // SAFETY: validated nonnull options remains borrowed above.
                let options = Options::from(unsafe { *options });
                state.requests[index] = Some(Request {
                    phase: Phase::Waiting,
                    id,
                    options,
                    selected: None,
                    direct: false,
                    delivery_only: false,
                    capture: 0,
                    delivery: 0,
                });
                state.current = id;
                if state.notify(
                    c"request",
                    state.requests[index].as_ref().expect("new request"),
                ) {
                    true
                } else {
                    state.requests[index] = None;
                    false
                }
            } else {
                false
            }
        } else {
            false
        }
    } else {
        false
    };
    if sent {
        0
    } else {
        // SAFETY: unchanged original arguments; no registry lock spans native code.
        unsafe { (native.show)(options) }
    }
}
/// # Safety
/// Arguments follow the reviewed native constructor ABI. On entry, the native
/// constructor's FD ownership contract applies even when construction fails.
#[unsafe(no_mangle)]
pub(crate) unsafe extern "C" fn dpe_picker_new(
    options: *const CaptureOptions,
    output: *mut *mut c_void,
) -> i32 {
    let Some(native) = NATIVE.get() else {
        return -libc::EINVAL;
    };
    let mut id = 0;
    let mut direct = false;
    let mut probe = ptr::null_mut();
    {
        let mut state = lock();
        if !options.is_null()
            // SAFETY: caller borrows this reviewed options record.
            && !unsafe { (*options).config.is_null() }
        {
            // SAFETY: the reviewed constructor borrows this configuration.
            let config = unsafe { *(*options).config };
            let selected = state.requests.iter().position(|request| {
                request.as_ref().is_some_and(|r| {
                    matches!(r.phase, Phase::Selected | Phase::Cancelled)
                        && r.selected
                            .is_some_and(|s| s.fd == config.fd && s.node == config.node)
                })
            });
            if let Some(index) = selected {
                let request = state.requests[index].as_mut().expect("selected request");
                if request.phase == Phase::Cancelled
                    || !request.selected.is_some_and(Identity::matches)
                {
                    if !output.is_null() {
                        // SAFETY: optional constructor result is writable.
                        unsafe {
                            output.write(ptr::null_mut());
                        }
                    }
                    return -libc::ECANCELED;
                }
                request.phase = Phase::Starting;
                request.selected = None; // Transfer before any native reentry.
                id = request.id;
                direct = request.direct;
                if !direct {
                    // SAFETY: options and process-lifetime frame functions were validated.
                    probe =
                        unsafe { delivery::dpe_delivery_create(id, options, true, native.frames) };
                    request.delivery = probe as usize;
                }
            }
            if id == 0 && config.node == dpe_core::direct::NODE {
                if !output.is_null() {
                    // SAFETY: optional constructor result is writable.
                    unsafe {
                        output.write(ptr::null_mut());
                    }
                }
                return -libc::ECANCELED;
            }
            if id == 0
                && state.enabled
                && config.fd >= 0
                && config.node != u32::MAX
                && let Some(index) = state.requests.iter().position(Option::is_none)
            {
                let candidate = state.next_id();
                // SAFETY: native owns the portal FD; only its callback is wrapped.
                probe = unsafe {
                    delivery::dpe_delivery_create(candidate, options, false, native.frames)
                };
                if !probe.is_null() {
                    id = candidate;
                    state.requests[index] = Some(Request {
                        phase: Phase::Starting,
                        id,
                        options: Options {
                            source: 0,
                            padding: 0,
                            callback: None,
                            data: 0,
                        },
                        selected: None,
                        direct: false,
                        delivery_only: true,
                        capture: 0,
                        delivery: probe as usize,
                    });
                }
            }
        }
    }
    let mut adapted = std::mem::MaybeUninit::<CaptureOptions>::uninit();
    // SAFETY: the adapter writes the complete output on success.
    let options = if !probe.is_null()
        && unsafe { delivery::dpe_delivery_adapt(probe, options, adapted.as_mut_ptr()) }
    {
        adapted.as_ptr()
    } else {
        options
    };
    let result = if direct {
        // SAFETY: constructor accepts the same reviewed record and output.
        unsafe { crate::worker::dpe_direct_capture_new(options.cast(), output) }
    } else {
        // SAFETY: unchanged reviewed C ABI; no registry lock spans construction.
        unsafe { (native.new)(options, output) }
    };
    let mut retired = 0;
    if id != 0 {
        let mut state = lock();
        if let Some(index) = state.index(id)
            && state.requests[index]
                .as_ref()
                .is_some_and(|r| r.phase == Phase::Starting)
        {
            if result == 0 && !output.is_null()
                // SAFETY: successful constructor initializes its result.
                && !unsafe { *output }.is_null()
            {
                let request = state.requests[index].as_mut().expect("starting request");
                // SAFETY: successful constructor wrote this output.
                request.capture = unsafe { *output } as usize;
                request.phase = Phase::Active;
                state.notify(
                    c"started",
                    state.requests[index].as_ref().expect("active request"),
                );
            } else {
                state.notify(
                    c"stopped",
                    state.requests[index].as_ref().expect("failed request"),
                );
                retired = state.requests[index]
                    .take()
                    .expect("failed request")
                    .delivery;
            }
        }
    }
    delivery::dpe_delivery_destroy(retired as *mut c_void);
    result
}
/// # Safety
/// Caller serializes operations on this live reviewed capture object.
#[unsafe(no_mangle)]
pub(crate) unsafe extern "C" fn dpe_picker_update(
    capture: *mut c_void,
    options: *const CaptureOptions,
) -> i32 {
    let mut status = 0;
    // SAFETY: owned handles use the same scalar/record ABI and status output.
    if unsafe { crate::worker::dpe_direct_capture_update(capture, options.cast(), &mut status) } {
        return status;
    }
    if pointer_reserved(capture as usize) {
        return -libc::EINVAL;
    }
    let Some(native) = NATIVE.get() else {
        return -libc::EINVAL;
    };
    let state = lock();
    let probe = state
        .requests
        .iter()
        .flatten()
        .find(|r| r.phase == Phase::Active && r.capture == capture as usize)
        .map_or(0, |r| r.delivery);
    // The reviewed native update only enqueues: it cannot call the frame/picker
    // callbacks inline or join. This lock serializes update with retirement.
    // SAFETY: callers borrow these native arguments; probe is pinned by state.
    unsafe {
        if probe != 0 && !options.is_null() {
            delivery::dpe_delivery_update(
                probe as *mut c_void,
                capture,
                options,
                Some(native.update),
            )
        } else {
            (native.update)(capture, options)
        }
    }
}
/// # Safety
/// Native destruction posts stop and joins before any callback lease is retired.
#[unsafe(no_mangle)]
pub(crate) unsafe extern "C" fn dpe_picker_destroy(capture: *mut c_void) -> i32 {
    let Some(native) = NATIVE.get() else {
        return -libc::EINVAL;
    };
    {
        let state = lock();
        for request in state
            .requests
            .iter()
            .flatten()
            .filter(|r| r.phase == Phase::Active && r.capture == capture as usize)
        {
            delivery::dpe_delivery_wake(request.delivery as *mut c_void, true);
        }
    }
    let mut result = 0;
    // SAFETY: owned handles follow the same opaque destruction ABI.
    if !unsafe { crate::worker::dpe_direct_capture_destroy(capture, &mut result) } {
        result = if pointer_reserved(capture as usize) {
            0
        } else {
            // SAFETY: unowned pointer is passed through to its native owner, unlocked.
            unsafe { (native.destroy)(capture) }
        };
    }
    let retired = {
        let mut state = lock();
        if let Some(index) = state.requests.iter().position(|r| {
            r.as_ref()
                .is_some_and(|r| r.phase == Phase::Active && r.capture == capture as usize)
        }) {
            state.notify(
                c"stopped",
                state.requests[index].as_ref().expect("active request"),
            );
            state.requests[index]
                .take()
                .expect("active request")
                .delivery
        } else {
            0
        }
    };
    delivery::dpe_delivery_destroy(retired as *mut c_void);
    result
}
fn open_remote(path: &CStr, direct: bool) -> Option<OwnedFd> {
    use std::os::fd::FromRawFd;
    let bytes = path.to_bytes();
    // SAFETY: zero-initialized system structs contain no invalid bit patterns.
    let mut address = unsafe { std::mem::zeroed::<libc::sockaddr_un>() };
    if bytes.first() != Some(&b'/') || bytes.len() >= address.sun_path.len() {
        return None;
    }
    // SAFETY: lstat initializes this complete system record on success.
    let mut stat = unsafe { std::mem::zeroed::<libc::stat>() };
    // SAFETY: path is a bounded terminated string; stat is writable. lstat
    // rejects symlinks, and SO_PEERCRED below checks the connected peer too.
    if unsafe { libc::lstat(path.as_ptr(), &mut stat) } != 0 || stat.st_mode & libc::S_IFMT != libc::S_IFSOCK
        // SAFETY: scalar effective-UID query has no pointer arguments.
        || stat.st_uid != unsafe { libc::geteuid() }
    {
        return None;
    }
    address.sun_family = libc::AF_UNIX as _;
    for (to, from) in address.sun_path.iter_mut().zip(bytes) {
        *to = *from as _;
    }
    // SAFETY: create a new exclusively owned nonblocking local socket.
    let raw = unsafe {
        libc::socket(
            libc::AF_UNIX,
            (if direct {
                libc::SOCK_SEQPACKET
            } else {
                libc::SOCK_STREAM
            }) | libc::SOCK_CLOEXEC
                | libc::SOCK_NONBLOCK,
            0,
        )
    };
    if raw < 0 {
        return None;
    }
    // SAFETY: successful socket creates a unique descriptor owned by this guard.
    let fd = unsafe { OwnedFd::from_raw_fd(raw) };
    // SAFETY: address is one initialized sockaddr_un of the exact supplied size.
    if unsafe {
        libc::connect(
            raw,
            (&raw const address).cast(),
            size_of::<libc::sockaddr_un>() as _,
        )
    } != 0
    {
        if !matches!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::EINPROGRESS | libc::EAGAIN)
        ) {
            return None;
        }
        let mut wait = libc::pollfd {
            fd: raw,
            events: libc::POLLOUT,
            revents: 0,
        };
        let mut error = 0i32;
        let mut length = size_of::<i32>() as libc::socklen_t;
        // SAFETY: poll borrows one writable record; getsockopt borrows an exact
        // integer output. Bound connection establishment to 250 milliseconds.
        if unsafe { libc::poll(&mut wait, 1, 250) } <= 0
            // SAFETY: this owned socket writes one bounded integer option.
            || unsafe {
                libc::getsockopt(
                    raw,
                    libc::SOL_SOCKET,
                    libc::SO_ERROR,
                    (&raw mut error).cast(),
                    &mut length,
                )
            } != 0
            || error != 0
        {
            return None;
        }
    }
    // SAFETY: zero is a valid initial system credentials record.
    let mut peer = unsafe { std::mem::zeroed::<libc::ucred>() };
    let mut length = size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: bounded credentials output belongs to this owned socket.
    if unsafe { libc::getsockopt(raw, libc::SOL_SOCKET, libc::SO_PEERCRED, (&raw mut peer).cast(), &mut length) } != 0
        || length as usize != size_of::<libc::ucred>()
        // SAFETY: scalar effective-UID query and descriptor flag update.
        || peer.uid != unsafe { libc::geteuid() } || unsafe { libc::fcntl(raw, libc::F_SETFL, 0) } < 0
    {
        return None;
    }
    Some(fd)
}
/// # Safety
/// Action and path are readable terminated strings, borrowed for this call.
#[unsafe(no_mangle)]
pub(crate) unsafe extern "C" fn dpe_picker_finish(
    id: u32,
    action: *const c_char,
    path: *const c_char,
    node: u32,
) -> bool {
    if action.is_null() || path.is_null() {
        return false;
    }
    // SAFETY: API requires terminated strings, not arbitrary foreign memory.
    let (action, path) = unsafe { (CStr::from_ptr(action), CStr::from_ptr(path)) };
    if ![c"selected", c"native", c"cancel", c"error"].contains(&action) || node == u32::MAX {
        return false;
    }
    let direct = action == c"selected" && node == dpe_core::direct::NODE;
    #[cfg(feature = "capture-gpu")]
    if direct && !crate::direct_bridge::dpe_direct_capture_ready() {
        return false;
    }
    #[cfg(not(feature = "capture-gpu"))]
    if direct {
        return false;
    }
    let fd = if action == c"selected" {
        let Some(fd) = open_remote(path, direct) else {
            return false;
        };
        Some(fd)
    } else {
        None
    };
    let selected = if let Some(fd) = &fd {
        let Some(identity) = Identity::checked(fd.as_raw_fd(), node) else {
            return false;
        };
        Some(identity)
    } else {
        None
    };
    let mut result = PickerResult {
        success: false,
        padding: [0; 3],
        config: Configuration { fd: -1, node: 0 },
        padding2: 0,
        error: ptr::null(),
    };
    let options = {
        let mut state = lock();
        let Some(index) = state.index(id) else {
            return false;
        };
        if state.current != id
            || !state.enabled
            || state.requests[index]
                .as_ref()
                .is_none_or(|r| r.phase != Phase::Waiting)
        {
            return false;
        }
        let options = state.requests[index]
            .as_ref()
            .expect("waiting request")
            .options;
        if let Some(selected) = selected {
            for other in 0..state.requests.len() {
                if other != index
                    && state.requests[other]
                        .as_ref()
                        .is_some_and(|r| r.phase == Phase::Selected)
                {
                    state.cancel(other);
                }
            }
            let request = state.requests[index].as_mut().expect("waiting request");
            request.selected = Some(selected);
            request.direct = direct;
            request.phase = Phase::Selected;
            result.success = true;
            result.config = Configuration {
                fd: fd.expect("selected socket").into_raw_fd(),
                node,
            };
        } else {
            if action != c"native" {
                state.notify(
                    c"cancelled",
                    state.requests[index].as_ref().expect("waiting request"),
                );
            }
            state.requests[index] = None;
            if action == c"error" {
                result.error = c"The selected capture source could not be started.".as_ptr();
            }
        }
        options.native()
    };
    if action == c"native" {
        if let Some(native) = NATIVE.get() {
            // SAFETY: copied reviewed options retain the native callback owner;
            // no registry lock spans the native fallback picker call.
            unsafe {
                (native.show)(&options);
            }
        }
    } else if let Some(callback) = options.callback {
        // SAFETY: the native request owns this callback/data until completion;
        // result is a live borrowed reviewed C record, outside the registry lock.
        unsafe {
            callback(&result, options.data);
        }
    }
    true
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_picker_cancel(id: u32) -> bool {
    let callback = {
        let mut state = lock();
        let Some(index) = state.index(id) else {
            return false;
        };
        let request = state.requests[index].as_ref().expect("matched request");
        if !matches!(request.phase, Phase::Waiting | Phase::Selected) {
            return false;
        }
        let callback =
            (request.phase == Phase::Waiting && state.current == id).then_some(request.options);
        state.cancel(index);
        callback
    };
    if let Some(options) = callback
        && let Some(callback) = options.callback
    {
        let result = PickerResult {
            success: false,
            padding: [0; 3],
            config: Configuration { fd: -1, node: 0 },
            padding2: 0,
            error: ptr::null(),
        };
        // SAFETY: native request owns this callback/data through completion, and
        // no registry lock spans the synchronously reentrant native callback.
        unsafe {
            callback(&result, options.data as *const c_void);
        }
    }
    true
}
