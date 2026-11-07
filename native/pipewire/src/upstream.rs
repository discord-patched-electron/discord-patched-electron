// SPDX-License-Identifier: MIT
//! Stream owners around the pure rate model. Registries retain callback state,
//! not borrowed stream storage. Native calls always happen outside Rust locks.
use crate::{
    ffi as f,
    pod::{Format, MAX_BODY, Owned},
    rates::{Mode, State, Totals},
};
use dpe_core::handles::Handles;
use std::{
    cell::UnsafeCell,
    collections::HashMap,
    ffi::{c_char, c_void},
    ptr::{self, NonNull},
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicU64, AtomicUsize, Ordering},
    },
    thread::ThreadId,
};

pub type Connect =
    unsafe extern "C" fn(*mut f::pw_stream, u32, u32, u32, *const *const f::spa_pod, u32) -> i32;
pub type Update = unsafe extern "C" fn(*mut f::pw_stream, *const *const f::spa_pod, u32) -> i32;
#[derive(Clone, Copy)]
#[repr(C)]
pub struct Native {
    pub connect: Connect,
    pub update: Update,
    pub listen: unsafe extern "C" fn(
        *mut f::pw_stream,
        *mut f::spa_hook,
        *const f::pw_stream_events,
        *mut c_void,
    ),
    pub core: Option<unsafe extern "C" fn(*mut f::pw_stream) -> *mut f::pw_core>,
    pub context: Option<unsafe extern "C" fn(*mut f::pw_core) -> *mut f::pw_context>,
    pub loop_: Option<unsafe extern "C" fn(*mut f::pw_context) -> *mut f::pw_loop>,
    pub state: Option<unsafe extern "C" fn(*mut f::pw_stream, *mut *const c_char) -> i32>,
}
static NATIVE: OnceLock<Native> = OnceLock::new();
static MODE: OnceLock<Mode> = OnceLock::new();
static REQUESTED: AtomicU64 = AtomicU64::new(0);
static LATE_RATES: AtomicU64 = AtomicU64::new(0);
static TOTALS: Mutex<Totals> = Mutex::new(Totals {
    observed: 0,
    supported: 0,
    changes: 0,
    restored: 0,
    failures: 0,
    bypassed: 0,
    timeouts: 0,
    asynchronous_errors: 0,
    initial_preferences: 0,
    initial_without_rate: 0,
});
static STREAMS: OnceLock<Mutex<HashMap<usize, usize>>> = OnceLock::new();
static SESSIONS: OnceLock<Handles<Arc<Session>>> = OnceLock::new();
fn streams() -> &'static Mutex<HashMap<usize, usize>> {
    STREAMS.get_or_init(|| Mutex::new(HashMap::new()))
}
fn sessions() -> &'static Handles<Arc<Session>> {
    SESSIONS.get_or_init(|| Handles::new(16))
}
fn session(token: usize) -> Option<Arc<Session>> {
    sessions().get(token)
}
fn find(stream: *mut f::pw_stream) -> Option<Arc<Session>> {
    let token = *streams()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .get(&(stream as usize))?;
    session(token)
}
fn now() -> u64 {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: initialized fixed-size output for a scalar monotonic query.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } == 0 {
        (time.tv_sec as u64)
            .saturating_mul(1_000_000_000)
            .saturating_add(time.tv_nsec as u64)
    } else {
        0
    }
}
fn accumulate(before: Totals, after: Totals) {
    if before == after {
        return;
    }
    let mut total = TOTALS
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    macro_rules! add { ($($field:ident),*) => { $(total.$field = total.$field.saturating_add(after.$field.saturating_sub(before.$field));)* }; }
    add!(
        observed,
        supported,
        changes,
        restored,
        failures,
        bypassed,
        timeouts,
        asynchronous_errors,
        initial_preferences,
        initial_without_rate
    );
}
struct Hook(UnsafeCell<f::spa_hook>);
impl Hook {
    fn new() -> Self {
        Self(UnsafeCell::new(f::spa_hook::default()))
    }
    unsafe fn remove(&self) {
        let hook = self.0.get();
        // SAFETY: caller is inside the stream's serialized destruction/owner
        // context. This registration is removed once, before its allocation is
        // retired. No Rust reference aliases the C-owned link mutations.
        unsafe {
            let previous = (*hook).link.prev;
            let next = (*hook).link.next;
            if !previous.is_null() && !next.is_null() {
                (*previous).next = next;
                (*next).prev = previous;
                (*hook).link.prev = ptr::null_mut();
                (*hook).link.next = ptr::null_mut();
            }
            if let Some(removed) = (*hook).removed {
                removed(hook);
            }
        }
    }
}
#[derive(Clone, Copy)]
struct Loop {
    data: *mut c_void,
    add: unsafe extern "C" fn(
        *mut c_void,
        f::spa_source_timer_func_t,
        *mut c_void,
    ) -> *mut f::spa_source,
    update: unsafe extern "C" fn(
        *mut c_void,
        *mut f::spa_source,
        *mut f::timespec,
        *mut f::timespec,
        bool,
    ) -> i32,
    destroy: unsafe extern "C" fn(*mut c_void, *mut f::spa_source),
}
impl Loop {
    unsafe fn borrowed(loop_: *mut f::pw_loop) -> Option<Self> {
        // SAFETY: the capture owner keeps its PipeWire loop alive through the
        // stream's destroy callback and this timer's synchronous removal.
        let utils = unsafe { loop_.as_ref()?.utils.as_ref()? };
        let methods = NonNull::new(
            utils
                .iface
                .cb
                .funcs
                .cast_mut()
                .cast::<f::spa_loop_utils_methods>(),
        )?;
        // SAFETY: read only documented v0 slots; a future full method table
        // need not exist at this foreign prefix address.
        let (add, update, destroy) = unsafe {
            (
                ptr::addr_of!((*methods.as_ptr()).add_timer).read(),
                ptr::addr_of!((*methods.as_ptr()).update_timer).read(),
                ptr::addr_of!((*methods.as_ptr()).destroy_source).read(),
            )
        };
        Some(Self {
            data: utils.iface.cb.data,
            add: add?,
            update: update?,
            destroy: destroy?,
        })
    }
    fn timer(self, token: usize) -> Option<Timer> {
        // SAFETY: retained loop and registered callback token outlive the source;
        // no Rust lock is held while the native API may emit a callback.
        let source = unsafe { (self.add)(self.data, Some(tick), token as *mut c_void) };
        let timer = Timer {
            loop_: self,
            source: NonNull::new(source)?,
        };
        let mut interval = f::timespec {
            tv_sec: 0,
            tv_nsec: 100_000_000,
        };
        let mut first = interval;
        // SAFETY: initialized relative timer values and one live owned source.
        if unsafe {
            (self.update)(
                self.data,
                timer.source.as_ptr(),
                &mut first,
                &mut interval,
                false,
            )
        } < 0
        {
            return None;
        }
        Some(timer)
    }
}
struct Timer {
    loop_: Loop,
    source: NonNull<f::spa_source>,
}
impl Drop for Timer {
    fn drop(&mut self) {
        // SAFETY: called by the serialized stream owner/destroy callback before
        // loop retirement. Removal revokes callbacks before this owner drops.
        unsafe {
            (self.loop_.destroy)(self.loop_.data, self.source.as_ptr());
        }
    }
}
struct Session {
    stream: usize,
    owner: ThreadId,
    model: Mutex<State>,
    hook: Box<Hook>,
    timer: Mutex<Option<Timer>>,
    token: AtomicUsize,
}
// SAFETY: the model and resource transitions are locked, and no Rust reference
// to the foreign hook/stream storage escapes. Connect/update/timer calls require
// the creating thread. Native destroy callbacks may run on another thread under
// PipeWire's own stream serialization, and synchronously detach both resources
// before the externally owned stream/loop retire. No other thread uses them.
unsafe impl Send for Session {}
// SAFETY: same native serialization contract; shared Rust state is behind Mutex.
unsafe impl Sync for Session {}
impl Session {
    fn edit<T>(&self, access: impl FnOnce(&mut State) -> T) -> T {
        let (before, after, result) = {
            let mut state = self
                .model
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let before = state.totals;
            let result = access(&mut state);
            (before, state.totals, result)
        };
        accumulate(before, after);
        result
    }
    fn own_thread(&self) -> bool {
        self.owner == std::thread::current().id()
    }
}
fn retire(token: usize) {
    let Some(session) = sessions().remove(token) else {
        return;
    };
    {
        let mut streams = streams()
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if streams.get(&session.stream) == Some(&token) {
            streams.remove(&session.stream);
        }
    }
    session.edit(State::destroy);
    let timer = session
        .timer
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .take();
    drop(timer);
    // SAFETY: invoked by connect failure/replacement on the stream owner, or by
    // the native stream's serialized destroy event. Remove before freeing hook.
    unsafe {
        session.hook.remove();
    }
}
unsafe extern "C" fn destroyed(data: *mut c_void) {
    retire(data as usize);
}
unsafe fn format<'a>(pod: *const f::spa_pod) -> Option<Format<'a>> {
    if pod.is_null() {
        return None;
    }
    // SAFETY: native parameter API supplies a live POD header and its declared
    // initialized body for this synchronous callback. Reject oversized lengths
    // before constructing a bounded byte view; retain no borrowed native memory.
    let size = unsafe { ptr::addr_of!((*pod).size).read_unaligned() } as usize;
    if size > MAX_BODY {
        return None;
    }
    // SAFETY: same native POD lifetime/size contract, now checked against our cap.
    Format::parse(unsafe { std::slice::from_raw_parts(pod.cast::<u8>(), size + 8) })
}
unsafe fn offers(params: *const *const f::spa_pod, count: u32) -> Option<(Vec<Owned>, Vec<usize>)> {
    if params.is_null() || count == 0 || count > 32 {
        return None;
    }
    let mut copied = Vec::new();
    let mut positions = Vec::new();
    // SAFETY: native call supplies count borrowed POD pointers for its duration.
    for (index, &pointer) in unsafe { std::slice::from_raw_parts(params, count as usize) }
        .iter()
        .enumerate()
    {
        // SAFETY: each optional native POD remains live through this call.
        if let Some(pod) = unsafe { format(pointer) }.filter(|pod| pod.is_offer()) {
            copied.push(pod.owned());
            positions.push(index);
        }
    }
    (!copied.is_empty()).then_some((copied, positions))
}
unsafe extern "C" fn negotiated(data: *mut c_void, id: u32, pod: *const f::spa_pod) {
    if id != f::DPE_PARAM_FORMAT || pod.is_null() {
        return;
    }
    let Some(session) = session(data as usize).filter(|session| session.own_thread()) else {
        return;
    };
    // SAFETY: PipeWire owns this format through the callback; edit stores only
    // decoded scalar values, and no borrow spans another native function call.
    let format = unsafe { format(pod) };
    session.edit(|state| state.observe(format));
}
unsafe extern "C" fn state_changed(data: *mut c_void, _: i32, state: i32, _: *const c_char) {
    if state != f::DPE_STREAM_ERROR {
        return;
    }
    if let Some(session) = session(data as usize).filter(|session| session.own_thread()) {
        session.edit(|state| state.asynchronous_error(REQUESTED.load(Ordering::Relaxed)));
    }
}
unsafe extern "C" fn tick(data: *mut c_void, _: u64) {
    let Some(session) = session(data as usize).filter(|session| session.own_thread()) else {
        return;
    };
    let Some(native) = NATIVE.get() else {
        return;
    };
    // SAFETY: native timer runs within the live stream's owning loop context.
    let stream_error = native.state.is_some_and(|get| unsafe {
        get(session.stream as *mut f::pw_stream, ptr::null_mut()) == f::DPE_STREAM_ERROR
    });
    let publication =
        session.edit(|state| state.tick(REQUESTED.load(Ordering::Relaxed), now(), stream_error));
    let Some(publication) = publication else {
        return;
    };
    let mut pointers = [ptr::null(); 32];
    for (slot, pod) in pointers.iter_mut().zip(publication.offers()) {
        *slot = pod.as_ptr().cast::<f::spa_pod>();
    }
    // SAFETY: typed original and live stream; publication owns aligned PODs
    // through synchronous/reentrant callbacks and no Rust lock spans the call.
    let result = unsafe {
        (native.update)(
            session.stream as *mut f::pw_stream,
            pointers.as_ptr(),
            publication.offers().len() as u32,
        )
    };
    session.edit(|state| state.published(publication, result));
}
fn events() -> &'static f::pw_stream_events {
    static EVENTS: OnceLock<f::pw_stream_events> = OnceLock::new();
    EVENTS.get_or_init(|| f::pw_stream_events {
        version: f::DPE_STREAM_EVENTS_VERSION,
        destroy: Some(destroyed),
        state_changed: Some(state_changed),
        param_changed: Some(negotiated),
        ..Default::default()
    })
}
/// # Safety
/// Call only after the complete reviewed native linkage has been validated.
/// Libraries supplying every function must remain loaded for process lifetime.
/// Serialize initialization before any redirected native call can run.
pub unsafe fn initialize(native: Native, mode: Mode) -> bool {
    if let Some(existing) = NATIVE.get() {
        return MODE.get() == Some(&mode)
            && existing.connect as usize == native.connect as usize
            && existing.update as usize == native.update as usize;
    }
    MODE.set(mode).is_ok() && NATIVE.set(native).is_ok()
}
pub fn request(fps: u32) {
    if fps > 240 {
        return;
    }
    if REQUESTED
        .try_update(Ordering::Relaxed, Ordering::Relaxed, |old| {
            (old & 255 != u64::from(fps)).then_some((old.wrapping_add(256) & !255) | u64::from(fps))
        })
        .is_ok()
        && MODE.get() == Some(&Mode::Initial)
        && !streams()
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .is_empty()
    {
        LATE_RATES.fetch_add(1, Ordering::Relaxed);
    }
}
/// # Safety
/// Reviewed call site supplies a live stream and its count valid POD pointers.
/// Its native owner serializes stream calls and destroy callbacks; its loop
/// remains live until destruction completes. No realtime callbacks are tracked.
pub unsafe extern "C" fn connect(
    stream: *mut f::pw_stream,
    direction: u32,
    target: u32,
    flags: u32,
    params: *const *const f::spa_pod,
    count: u32,
) -> i32 {
    let Some(native) = NATIVE.get() else {
        return -libc::ENOSYS;
    };
    let pass = || {
        // SAFETY: unchanged call arguments/ownership pass to the typed original.
        unsafe { (native.connect)(stream, direction, target, flags, params, count) }
    };
    if direction != f::DPE_STREAM_INPUT || flags != f::DPE_STREAM_AUTOCONNECT {
        return pass();
    }
    // SAFETY: borrowed native parameter inventory stays live until connect returns.
    let Some((original, positions)) = (unsafe { offers(params, count) }) else {
        return pass();
    };
    let mode = *MODE.get().expect("native initialized with mode");
    let Some(model) = State::new(mode, original) else {
        return pass();
    };
    // Reconnecting a reused stream retires its old generation before publishing
    // the replacement; in-flight calls still retain their old offers by Arc.
    if let Some(previous) = find(stream) {
        retire(previous.token.load(Ordering::Relaxed));
    }
    let owner = Arc::new(Session {
        stream: stream as usize,
        owner: std::thread::current().id(),
        model: Mutex::new(model),
        hook: Box::new(Hook::new()),
        timer: Mutex::new(None),
        token: AtomicUsize::new(0),
    });
    let admission = {
        let mut streams = streams()
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        sessions().insert(owner.clone()).map(|token| {
            owner.token.store(token.get(), Ordering::Relaxed);
            streams.insert(stream as usize, token.get());
            token.get()
        })
    };
    let Ok(token) = admission else {
        return pass();
    };
    let preferred =
        owner.edit(|state| state.initial((REQUESTED.load(Ordering::Relaxed) & 255) as u32));
    let mut rewritten = [ptr::null(); 32];
    if let Some(preferred) = &preferred {
        // SAFETY: count was bounded and validated by offers above.
        rewritten[..count as usize]
            .copy_from_slice(unsafe { std::slice::from_raw_parts(params, count as usize) });
        for (&index, pod) in positions.iter().zip(preferred) {
            rewritten[index] = pod.as_ptr().cast();
        }
    }
    // SAFETY: pinned hook allocation and globally registered token outlive the
    // listener. Both listener and connect may synchronously destroy the stream.
    unsafe {
        (native.listen)(stream, owner.hook.0.get(), events(), token as *mut c_void);
    }
    if owner.edit(|state| state.destroyed()) {
        return -libc::EPIPE;
    }
    // SAFETY: preferred offers stay owned on this stack through callbacks;
    // all unchanged parameters retain the original borrowed pointers/count.
    let result = unsafe {
        (native.connect)(
            stream,
            direction,
            target,
            flags,
            if preferred.is_some() {
                rewritten.as_ptr()
            } else {
                params
            },
            count,
        )
    };
    if result < 0 {
        retire(token);
    } else if mode == Mode::Runtime && !owner.edit(|state| state.destroyed()) {
        // SAFETY: the successfully connected stream retains its core/loop.
        // Timer creation may reenter; commit only to a still-live owner.
        let timer = unsafe {
            native
                .core
                .and_then(|get| NonNull::new(get(stream)))
                .and_then(|core| {
                    native
                        .context
                        .and_then(|get| NonNull::new(get(core.as_ptr())))
                })
                .and_then(|context| {
                    native
                        .loop_
                        .and_then(|get| Loop::borrowed(get(context.as_ptr())))
                })
                .and_then(|loop_| loop_.timer(token))
        };
        if !owner.edit(|state| state.destroyed()) {
            if timer.is_none() {
                owner.edit(State::disable_timer);
            }
            *owner
                .timer
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner) = timer;
        }
    }
    result
}
/// # Safety
/// Same serialized native stream and POD lifetime contract as connect.
pub unsafe extern "C" fn update(
    stream: *mut f::pw_stream,
    params: *const *const f::spa_pod,
    count: u32,
) -> i32 {
    let Some(native) = NATIVE.get() else {
        return -libc::ENOSYS;
    };
    let owner = (MODE.get() == Some(&Mode::Runtime))
        .then(|| find(stream))
        .flatten()
        .filter(|session| session.own_thread());
    let Some(owner) = owner else {
        // SAFETY: untouched native parameters pass through outside our scope.
        return unsafe { (native.update)(stream, params, count) };
    };
    // SAFETY: native parameter array remains live throughout this update call.
    let copies = unsafe { offers(params, count) }.map(|(offers, _)| offers);
    let ticket = owner.edit(State::begin_update);
    // SAFETY: no Rust lock spans callbacks; only the native owner can retire
    // this stream, and the registered state remains pinned by owner.
    let result = unsafe { (native.update)(stream, params, count) };
    owner.edit(|state| state.updated(ticket, copies, result));
    result
}
pub struct Snapshot {
    pub mode: Mode,
    pub requested: u32,
    pub active: usize,
    pub totals: Totals,
    pub preferred: u32,
    pub applied: u32,
    pub pending: u32,
    pub width: u32,
    pub height: u32,
    pub maximum: Option<crate::pod::Fraction>,
    pub late_rates: u64,
}
pub fn snapshot() -> Snapshot {
    let (active, only) = {
        let streams = streams()
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        (
            streams.len(),
            (streams.len() == 1)
                .then(|| streams.values().next().copied())
                .flatten(),
        )
    };
    let mut result = Snapshot {
        mode: MODE.get().copied().unwrap_or(Mode::Initial),
        requested: (REQUESTED.load(Ordering::Relaxed) & 255) as u32,
        active,
        totals: *TOTALS
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner),
        preferred: 0,
        applied: 0,
        pending: 0,
        width: 0,
        height: 0,
        maximum: None,
        late_rates: LATE_RATES.load(Ordering::Relaxed),
    };
    if let Some(owner) = only.and_then(session) {
        owner.edit(|state| {
            result.preferred = state.preferred;
            result.applied = state.applied;
            result.pending = state.pending_fps();
            (result.width, result.height) = state.dimensions;
            result.maximum = state.maximum;
        });
    }
    result
}
