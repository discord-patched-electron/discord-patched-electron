// SPDX-License-Identifier: MIT
//! Source event-loop ownership. Callbacks enqueue owned events; buffer
//! initialization stays synchronous as PipeWire's ALLOC_BUFFERS contract requires.
//! source state is processed after native dispatch, without mutable Rust borrows
//! spanning reentrant PipeWire calls. All objects are confined to one thread.
use crate::{
    ffi as f,
    pod::{MAX_BODY, Owned},
    source_pods::Pod,
};
use std::{
    cell::{Cell, RefCell, UnsafeCell},
    collections::{HashMap, VecDeque},
    ffi::{CStr, c_char, c_void},
    ptr::{self, NonNull},
    rc::Rc,
    sync::Once,
};
type Result<T> = std::result::Result<T, &'static str>;
type Iterate = unsafe extern "C" fn(*mut c_void, i32) -> i32;
type GetFd = unsafe extern "C" fn(*mut c_void) -> i32;
type Enter = unsafe extern "C" fn(*mut c_void);
/// Loop clones retain native fence registrations independently of the stream.
/// A removed stream buffer never implies GPU completion or drops such owners.
pub struct MainLoop {
    main: NonNull<f::pw_main_loop>,
    loop_: NonNull<f::pw_loop>,
    data: *mut c_void,
    iterate: Iterate,
    get_fd: GetFd,
    leave: Enter,
}
impl MainLoop {
    pub fn new() -> Result<Rc<Self>> {
        static INIT: Once = Once::new();
        INIT.call_once(|| {
            // SAFETY: one process initialization without argument rewriting.
            unsafe {
                f::pw_init(ptr::null_mut(), ptr::null_mut());
            }
        });
        // SAFETY: no properties are borrowed; success returns one loop owner.
        let main = NonNull::new(unsafe { f::pw_main_loop_new(ptr::null()) })
            .ok_or("PipeWire loop creation failed")?;
        let create = || -> Option<Self> {
            // SAFETY: the owned main loop retains its loop and control interface.
            let loop_ = NonNull::new(unsafe { f::pw_main_loop_get_loop(main.as_ptr()) })?;
            // SAFETY: immutable public interface prefix belongs to this live loop.
            let control = unsafe { loop_.as_ref().control.as_ref()? };
            let methods = NonNull::new(
                control
                    .iface
                    .cb
                    .funcs
                    .cast_mut()
                    .cast::<f::spa_loop_control_methods>(),
            )?;
            // SAFETY: read only the documented version-zero function slots;
            // never form a reference to an unverified complete future table.
            let (get_fd, enter, leave, iterate) = unsafe {
                (
                    ptr::addr_of!((*methods.as_ptr()).get_fd).read()?,
                    ptr::addr_of!((*methods.as_ptr()).enter).read()?,
                    ptr::addr_of!((*methods.as_ptr()).leave).read()?,
                    ptr::addr_of!((*methods.as_ptr()).iterate).read()?,
                )
            };
            // SAFETY: this thread exclusively enters its newly created loop.
            unsafe {
                enter(control.iface.cb.data);
            }
            Some(Self {
                main,
                loop_,
                data: control.iface.cb.data,
                iterate,
                get_fd,
                leave,
            })
        };
        if let Some(value) = create() {
            Ok(Rc::new(value))
        } else {
            // SAFETY: initialization failed before ownership transfer or enter.
            unsafe {
                f::pw_main_loop_destroy(main.as_ptr());
            }
            Err("PipeWire loop control unavailable")
        }
    }
    pub fn fd(&self) -> i32 {
        // SAFETY: immutable version-zero method and loop owner remain live.
        unsafe { (self.get_fd)(self.data) }
    }
    pub fn iterate(&self, timeout_ms: i32) -> Result<()> {
        if timeout_ms < -1 {
            return Err("Invalid PipeWire timeout");
        }
        // SAFETY: this !Send loop is entered/iterated on its owning thread.
        let status = unsafe { (self.iterate)(self.data, timeout_ms) };
        if status < 0 && status != -libc::EINTR {
            Err("PipeWire dispatch failed")
        } else {
            Ok(())
        }
    }
    /// Opaque pointer for the existing graphics/DRM adapter. Its caller must
    /// retain this Rc while any native watch is installed on the loop.
    pub fn as_ptr(&self) -> *mut f::pw_loop {
        self.loop_.as_ptr()
    }
}
impl Drop for MainLoop {
    fn drop(&mut self) {
        // SAFETY: the last thread-confined owner retires its own entered loop;
        // all stream and fence owners retain a clone until their cleanup ends.
        unsafe {
            (self.leave)(self.data);
            f::pw_main_loop_destroy(self.main.as_ptr());
        }
    }
}
struct BufferInner<T> {
    resident: Rc<Cell<usize>>,
    serial: u64,
    native: Cell<*mut f::pw_buffer>,
    checked_out: Cell<bool>,
    storage: T,
}
impl<T> Drop for BufferInner<T> {
    fn drop(&mut self) {
        self.resident.set(self.resident.get().saturating_sub(1));
    }
}
pub struct Buffer<T>(Rc<BufferInner<T>>);
impl<T> Clone for Buffer<T> {
    fn clone(&self) -> Self {
        Self(self.0.clone())
    }
}
impl<T> Buffer<T> {
    pub fn storage(&self) -> &T {
        &self.0.storage
    }
    pub fn serial(&self) -> u64 {
        self.0.serial
    }
    pub fn live(&self) -> bool {
        !self.0.native.get().is_null()
    }
    /// # Safety
    /// The caller uses this native buffer only until its next PipeWire call or
    /// dispatch, checks SPA bounds before access and never retains a Rust
    /// reference to foreign storage. Removal invalidates the pointer immediately.
    pub unsafe fn spa(&self) -> Option<NonNull<f::spa_buffer>> {
        let native = NonNull::new(self.0.native.get())?;
        // SAFETY: this callback record remains live before the next dispatch.
        NonNull::new(unsafe { native.as_ref().buffer })
    }
    pub fn set_header(&self, timestamp: u64, sequence: u64) -> Result<bool> {
        if !self.0.checked_out.get() {
            return Err("PipeWire buffer was not dequeued");
        }
        let timestamp = i64::try_from(timestamp).map_err(|_| "Invalid capture timestamp")?;
        // SAFETY: this live checked-out buffer is borrowed only within this
        // method, with no native call or dispatch that could remove it.
        let spa = unsafe { self.spa() }.ok_or("Retired PipeWire buffer")?;
        // SAFETY: same live native buffer prefix, before any next dispatch.
        let spa = unsafe { spa.as_ref() };
        if spa.n_metas > 64 || (spa.n_metas != 0 && spa.metas.is_null()) {
            return Err("Invalid PipeWire metadata extent");
        }
        if spa.n_metas == 0 {
            return Ok(false);
        }
        // SAFETY: native owns the bounded initialized metadata array while
        // this checked-out buffer remains live, with no reentrant native call.
        for meta in unsafe { std::slice::from_raw_parts(spa.metas, spa.n_metas as usize) } {
            if meta.type_ != f::DPE_POD_HEADER {
                continue;
            }
            let pointer = meta.data.cast::<f::spa_meta_header>();
            if pointer.is_null()
                || !pointer.is_aligned()
                || (meta.size as usize) < size_of::<f::spa_meta_header>()
            {
                return Err("Invalid PipeWire header");
            }
            // SAFETY: producer owns this checked-out buffer's complete writable
            // header. Write the record without borrowing uninitialized storage.
            unsafe {
                pointer.write(f::spa_meta_header {
                    pts: timestamp,
                    seq: sequence,
                    ..Default::default()
                });
            }
            return Ok(true);
        }
        Ok(false)
    }
    pub fn set_copy_size(&self, size: u32) -> Result<()> {
        if !self.0.checked_out.get() {
            return Err("PipeWire buffer was not dequeued");
        }
        // SAFETY: the owned checked-out buffer is borrowed only until return;
        // no native call or dispatch can invalidate it during these checks.
        let spa = unsafe { self.spa() }.ok_or("Retired PipeWire buffer")?;
        // SAFETY: native supplies this live prefix; inspect bounds before its data.
        let (count, data) = unsafe { (spa.as_ref().n_datas, spa.as_ref().datas) };
        if count != 1 || data.is_null() {
            return Err("Copy destination plane mismatch");
        }
        // SAFETY: exactly one initialized native data record, retained above.
        let (maximum, chunk) = unsafe { ((*data).maxsize, (*data).chunk) };
        if size > maximum || maximum == 0 || chunk.is_null() || !chunk.is_aligned() {
            return Err("Copy destination extent mismatch");
        }
        // SAFETY: this producer owns the complete writable chunk, with no
        // foreign reference retained and no native reentry during this write.
        unsafe {
            std::ptr::addr_of_mut!((*chunk).size).write(size);
        }
        Ok(())
    }
    pub fn set_relay_ready(&self, planes: u32) -> Result<()> {
        if !self.0.checked_out.get() {
            return Err("PipeWire buffer was not dequeued");
        }
        // SAFETY: checked-out native storage stays live without dispatch until
        // return. Validate every plane before publishing any chunk.
        let spa = unsafe { self.spa() }.ok_or("Retired PipeWire buffer")?;
        // SAFETY: inspect the retained native prefix before its bounded arrays.
        let (count, data) = unsafe { (spa.as_ref().n_datas, spa.as_ref().datas) };
        if !(1..=4).contains(&planes) || count != planes + 2 || data.is_null() {
            return Err("DMA-BUF plane mismatch");
        }
        for index in planes..count {
            // SAFETY: the two bounded trailing data records hold sync timelines.
            if unsafe { (*data.add(index as usize)).type_ } != f::DPE_RELAY_DATA {
                return Err("Invalid relay timeline data");
            }
        }
        let mut chunks = [ptr::null_mut(); 4];
        for index in 0..planes as usize {
            // SAFETY: index is inside the complete native data array.
            let (kind, chunk) = unsafe { ((*data.add(index)).type_, (*data.add(index)).chunk) };
            if kind != f::spa_data_type_SPA_DATA_DmaBuf
                || chunk.is_null()
                || !chunk.is_aligned()
                || chunks[..index].contains(&chunk)
            {
                return Err("Invalid DMA-BUF chunk");
            }
            chunks[index] = chunk;
        }
        for chunk in chunks.into_iter().take(planes as usize) {
            // SAFETY: all independent writable chunk records were checked;
            // opaque DMA-BUF payloads use a nonzero presence marker, not bytes.
            unsafe { ptr::addr_of_mut!((*chunk).size).write(1) };
        }
        Ok(())
    }
}
pub enum Event<T> {
    State {
        old: i32,
        next: i32,
        error: Option<String>,
    },
    Format(Option<Owned>),
    Added(Buffer<T>),
    Removed(u64),
    Process,
    Failed(&'static str),
}
type Factory<T> = dyn Fn(*mut f::spa_buffer, u64) -> Result<T>;
type Fixation = dyn Fn(&Owned) -> Result<Option<Vec<Pod>>>;
struct Events<T> {
    fixation: RefCell<Option<Rc<Fixation>>>,
    stream: Cell<*mut f::pw_stream>,
    resident: Rc<Cell<usize>>,
    factory: Box<Factory<T>>,
    listener: UnsafeCell<f::spa_hook>,
    core_listener: UnsafeCell<f::spa_hook>,
    values: RefCell<VecDeque<Event<T>>>,
    buffers: RefCell<HashMap<usize, Buffer<T>>>,
    maximum: usize,
    serial: Cell<u64>,
    process: Cell<bool>,
    failure: Cell<Option<&'static str>>,
    reported: Cell<bool>,
}
impl<T> Events<T> {
    fn new(maximum: usize, factory: Box<Factory<T>>) -> Self {
        Self {
            fixation: RefCell::new(None),
            stream: Cell::new(ptr::null_mut()),
            resident: Rc::new(Cell::new(0)),
            factory,
            listener: UnsafeCell::new(f::spa_hook::default()),
            core_listener: UnsafeCell::new(f::spa_hook::default()),
            values: RefCell::new(VecDeque::with_capacity(32)),
            buffers: RefCell::new(HashMap::with_capacity(maximum)),
            maximum,
            serial: Cell::new(0),
            process: Cell::new(false),
            failure: Cell::new(None),
            reported: Cell::new(false),
        }
    }
    fn fail(&self, error: &'static str) {
        if self.failure.get().is_none() {
            self.failure.set(Some(error));
        }
    }
    fn push(&self, event: Event<T>) {
        if self.failure.get().is_some() {
            return;
        }
        let mut values = self.values.borrow_mut();
        if values.len() >= 128 {
            self.fail("PipeWire event limit");
        } else {
            values.push_back(event);
        }
    }
    fn invalidate(&self) {
        for buffer in self.buffers.borrow_mut().drain().map(|(_, buffer)| buffer) {
            buffer.0.native.set(ptr::null_mut());
        }
    }
    fn pop(&self) -> Option<Event<T>> {
        if let Some(failure) = self.failure.get()
            && !self.reported.replace(true)
        {
            return Some(Event::Failed(failure));
        }
        let event = self.values.borrow_mut().pop_front()?;
        if matches!(event, Event::Process) {
            self.process.set(false);
        }
        Some(event)
    }
    fn checkout(&self, pointer: *mut f::pw_buffer) -> Result<Buffer<T>> {
        let buffer = self
            .buffers
            .borrow()
            .get(&(pointer as usize))
            .cloned()
            .ok_or("Unregistered PipeWire buffer")?;
        if buffer.0.checked_out.replace(true) {
            self.fail("PipeWire buffer dequeued twice");
            return Err("PipeWire buffer dequeued twice");
        }
        Ok(buffer)
    }
    fn returning(&self, buffer: &Buffer<T>) -> Result<*mut f::pw_buffer> {
        let pointer = buffer.0.native.get();
        if pointer.is_null()
            || self
                .buffers
                .borrow()
                .get(&(pointer as usize))
                .is_none_or(|owned| !Rc::ptr_eq(&owned.0, &buffer.0))
        {
            return Err("Retired or foreign PipeWire buffer");
        }
        if !buffer.0.checked_out.replace(false) {
            return Err("PipeWire buffer was not dequeued");
        }
        Ok(pointer)
    }
}
unsafe extern "C" fn state_changed<T: 'static>(
    data: *mut c_void,
    old: i32,
    next: i32,
    error: *const c_char,
) {
    // SAFETY: listener is bound to the !Send driver; its Rc survives native
    // dispatch and synchronous stream destruction. No callbacks execute JS.
    let state = unsafe { &*data.cast::<Events<T>>() };
    let error = if error.is_null() {
        None
    } else {
        // SAFETY: native PipeWire lends a terminated error string through this callback.
        let bytes = unsafe { CStr::from_ptr(error) }.to_bytes();
        Some(String::from_utf8_lossy(&bytes[..bytes.len().min(1024)]).into_owned())
    };
    state.push(Event::State { old, next, error });
}
unsafe extern "C" fn format_changed<T: 'static>(
    data: *mut c_void,
    id: u32,
    pod: *const f::spa_pod,
) {
    if id != f::DPE_PARAM_FORMAT {
        return;
    }
    // SAFETY: the entered-loop listener retains this complete Events owner.
    let state = unsafe { &*data.cast::<Events<T>>() };
    if pod.is_null() {
        state.push(Event::Format(None));
        return;
    }
    // SAFETY: native callback lends a complete SPA record for this invocation.
    let size = unsafe { (*pod).size } as usize;
    if !(8..=MAX_BODY).contains(&size) {
        state.fail("Invalid PipeWire format extent");
        return;
    }
    // SAFETY: the native record extent is valid through this callback, bounded
    // above, and copied immediately into aligned immutable Rust ownership.
    let bytes = unsafe { std::slice::from_raw_parts(pod.cast(), size + 8) };
    if let Some(owned) = Owned::copy_format(bytes) {
        // Modifier fixation must update Format before this callback returns:
        // PipeWire uses that synchronous parameter serial change as its ack.
        // Compute owned results without a stream call under any Rust borrow.
        let callback = state.fixation.borrow().clone();
        let action = callback.map(|callback| {
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| callback(&owned)))
        });
        match action {
            Some(Ok(Ok(Some(pods)))) => {
                let stream = state.stream.get();
                if stream.is_null() || pods.is_empty() || pods.len() > 8 {
                    state.fail("Invalid synchronous format update");
                    return;
                }
                let mut params = [ptr::null(); 8];
                for (target, pod) in params.iter_mut().zip(&pods) {
                    *target = pod.as_ptr();
                }
                // SAFETY: the current native callback pins this live stream;
                // owned aligned PODs outlive this synchronous update, with no
                // RefCell/model borrow across possible native reentry.
                if unsafe {
                    f::pw_stream_update_params(stream, params.as_mut_ptr(), pods.len() as u32)
                } < 0
                {
                    state.fail("Synchronous format update rejected");
                }
            }
            Some(Ok(Err(error))) => state.fail(error),
            Some(Err(_)) => state.fail("Format fixation panicked"),
            None | Some(Ok(Ok(None))) => state.push(Event::Format(Some(owned))),
        }
    } else {
        state.fail("Invalid PipeWire format");
    }
}
unsafe extern "C" fn added<T: 'static>(data: *mut c_void, native: *mut f::pw_buffer) {
    // SAFETY: the listener retains this owner during serialized main-loop dispatch.
    let state = unsafe { &*data.cast::<Events<T>>() };
    if native.is_null() {
        state.fail("Missing PipeWire buffer");
        return;
    }
    if state.failure.get().is_some() {
        return;
    }
    {
        let buffers = state.buffers.borrow();
        if state.resident.get() >= state.maximum || buffers.contains_key(&(native as usize)) {
            state.fail("PipeWire buffer admission failed");
            return;
        }
    }
    let Some(serial) = state.serial.get().checked_add(1) else {
        state.fail("PipeWire buffer serial exhausted");
        return;
    };
    state.serial.set(serial);
    // SAFETY: add_buffer lends a complete native buffer for synchronous
    // ALLOC_BUFFERS initialization. The unsafe constructor requires the factory
    // to fill it without dispatching/mutating this stream or retaining references.
    let spa = unsafe { (*native).buffer };
    if spa.is_null() {
        state.fail("Missing SPA buffer");
        return;
    }
    // A factory panic must never unwind across the native listener boundary.
    // No Events/model borrow spans GPU allocation or native loop registration.
    let storage = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        (state.factory)(spa, serial)
    }));
    let storage = match storage {
        Ok(Ok(storage)) => storage,
        Ok(Err(error)) => {
            state.fail(error);
            return;
        }
        Err(_) => {
            state.fail("Source buffer factory panicked");
            return;
        }
    };
    let buffer = Buffer(Rc::new(BufferInner {
        resident: state.resident.clone(),
        serial,
        native: Cell::new(native),
        checked_out: Cell::new(false),
        storage,
    }));
    state.resident.set(state.resident.get() + 1);
    state
        .buffers
        .borrow_mut()
        .insert(native as usize, buffer.clone());
    state.push(Event::Added(buffer));
}
unsafe extern "C" fn removed<T: 'static>(data: *mut c_void, native: *mut f::pw_buffer) {
    // SAFETY: the listener retains this owner through removal/destruction.
    let state = unsafe { &*data.cast::<Events<T>>() };
    let buffer = state.buffers.borrow_mut().remove(&(native as usize));
    if let Some(buffer) = buffer {
        buffer.0.native.set(ptr::null_mut());
        state.push(Event::Removed(buffer.serial()));
    }
}
unsafe extern "C" fn process<T: 'static>(data: *mut c_void) {
    // SAFETY: RT_PROCESS is never requested; native dispatch occurs on this loop.
    let state = unsafe { &*data.cast::<Events<T>>() };
    if !state.process.replace(true) {
        state.push(Event::Process);
    }
}
unsafe extern "C" fn core_error<T: 'static>(
    data: *mut c_void,
    id: u32,
    _sequence: i32,
    result: i32,
    message: *const c_char,
) {
    // SAFETY: Native retains this stable owner through core disconnect; the
    // native error message is terminated and borrowed only during this event.
    let state = unsafe { &*data.cast::<Events<T>>() };
    if !message.is_null() {
        use std::io::Write;
        // SAFETY: native lends this error's terminated string for the callback.
        let bytes = unsafe { CStr::from_ptr(message) }.to_bytes();
        let bytes = &bytes[..bytes.len().min(1024)];
        let _ = writeln!(
            std::io::stderr().lock(),
            "DPE PipeWire core {id}: {result}: {}",
            String::from_utf8_lossy(bytes)
        );
    }
    if id == 0 {
        state.fail("PipeWire core disconnected");
    }
}
fn stream_events<T: 'static>() -> f::pw_stream_events {
    f::pw_stream_events {
        version: f::DPE_STREAM_EVENTS_VERSION,
        destroy: None,
        state_changed: Some(state_changed::<T>),
        control_info: None,
        io_changed: None,
        param_changed: Some(format_changed::<T>),
        add_buffer: Some(added::<T>),
        remove_buffer: Some(removed::<T>),
        process: Some(process::<T>),
        drained: None,
        command: None,
        trigger_done: None,
    }
}
struct Native {
    callbacks: Box<f::pw_stream_events>,
    core_callbacks: Box<f::pw_core_events>,
    _listener_owner: Option<Rc<dyn std::any::Any>>,
    stream: Option<NonNull<f::pw_stream>>,
    core: Option<NonNull<f::pw_core>>,
    context: NonNull<f::pw_context>,
    loop_: Rc<MainLoop>,
}
impl Native {
    fn close_stream(&mut self) {
        if let Some(stream) = self.stream.take() {
            // SAFETY: this owner uniquely destroys its stream while listener
            // state and the entered loop remain live through native callbacks.
            unsafe {
                f::pw_stream_destroy(stream.as_ptr());
            }
        }
    }
}
impl Drop for Native {
    fn drop(&mut self) {
        self.close_stream();
        // SAFETY: disconnect before releasing the context/loop; each native
        // allocation has exactly one owner even during partial construction.
        unsafe {
            if let Some(core) = self.core.take() {
                f::pw_core_disconnect(core.as_ptr());
            }
            f::pw_context_destroy(self.context.as_ptr());
        }
    }
}
pub struct Driver<T: 'static> {
    native: Native,
    events: Rc<Events<T>>,
}
impl<T: 'static> Driver<T> {
    /// # Safety
    /// Factory must synchronously initialize each supplied SPA buffer, retain
    /// its descriptor/storage ownership in T, and never dispatch or mutate the
    /// stream during initialization. It must validate all SPA extents before
    /// writing them and retain no Rust reference to foreign storage afterward.
    pub unsafe fn new<F: Fn(*mut f::spa_buffer, u64) -> Result<T> + 'static>(
        loop_: Rc<MainLoop>,
        name: &CStr,
        properties: &CStr,
        maximum_buffers: usize,
        factory: F,
    ) -> Result<Self> {
        if !(1..=64).contains(&maximum_buffers) {
            return Err("Invalid source buffer bound");
        }
        // SAFETY: this loop is entered on this thread, with no borrowed properties.
        let context =
            NonNull::new(unsafe { f::pw_context_new(loop_.as_ptr(), ptr::null_mut(), 0) })
                .ok_or("PipeWire context creation failed")?;
        let mut native = Native {
            callbacks: Box::new(stream_events::<T>()),
            core_callbacks: Box::new(f::pw_core_events {
                version: 0,
                error: Some(core_error::<T>),
                ..Default::default()
            }),
            _listener_owner: None,
            context,
            loop_,
            core: None,
            stream: None,
        };
        // SAFETY: native owns the live context; the new core is adopted immediately.
        let core =
            NonNull::new(unsafe { f::pw_context_connect(context.as_ptr(), ptr::null_mut(), 0) })
                .ok_or("PipeWire connection failed")?;
        native.core = Some(core);
        let events = Rc::new(Events::new(maximum_buffers, Box::new(factory)));
        native._listener_owner = Some(events.clone());
        // SAFETY: pw_core's documented public interface is a spa_interface
        // prefix. Inspect only the version-zero add_listener slot, not an
        // unverified future complete method table. Native retains listener data
        // through disconnect, including every partial-construction failure.
        unsafe {
            let interface = &*core.as_ptr().cast::<f::spa_interface>();
            let methods = NonNull::new(interface.cb.funcs.cast_mut().cast::<f::pw_core_methods>())
                .ok_or("PipeWire core methods unavailable")?;
            let add = ptr::addr_of!((*methods.as_ptr()).add_listener)
                .read()
                .ok_or("PipeWire core listener unavailable")?;
            if add(
                interface.cb.data,
                events.core_listener.get(),
                &*native.core_callbacks,
                Rc::as_ptr(&events).cast_mut().cast(),
            ) < 0
            {
                return Err("PipeWire core listener rejected");
            }
        }
        // SAFETY: bounded terminated property input is copied by PipeWire.
        let properties = NonNull::new(unsafe { f::pw_properties_new_string(properties.as_ptr()) })
            .ok_or("Invalid source properties")?;
        // SAFETY: this core is live; stream_new takes the property owner on both
        // success and failure, and copies the borrowed terminated name.
        let stream = NonNull::new(unsafe {
            f::pw_stream_new(core.as_ptr(), name.as_ptr(), properties.as_ptr())
        })
        .ok_or("PipeWire stream creation failed")?;
        native.stream = Some(stream);
        events.stream.set(stream.as_ptr());
        // SAFETY: listener memory and callback data are stable Rc allocations,
        // retained until stream_destroy finishes. No RefCell borrow spans native calls.
        unsafe {
            f::pw_stream_add_listener(
                stream.as_ptr(),
                events.listener.get(),
                &*native.callbacks,
                Rc::as_ptr(&events).cast_mut().cast(),
            );
        }
        Ok(Self { native, events })
    }
    fn stream(&self) -> *mut f::pw_stream {
        self.native.stream.expect("live driver stream").as_ptr()
    }
    pub fn loop_owner(&self) -> Rc<MainLoop> {
        self.native.loop_.clone()
    }
    pub fn next_event(&self) -> Option<Event<T>> {
        self.events.pop()
    }
    /// The pure preparation callback returns owned PODs; it must not dispatch
    /// or mutate the stream. Only modifier fixation needs this synchronous ack.
    pub fn set_fixation(&self, callback: impl Fn(&Owned) -> Result<Option<Vec<Pod>>> + 'static) {
        *self.events.fixation.borrow_mut() = Some(Rc::new(callback));
    }
    pub fn has_events(&self) -> bool {
        !self.events.values.borrow().is_empty()
            || (self.events.failure.get().is_some() && !self.events.reported.get())
    }
    pub fn error(&self) -> Option<&'static str> {
        self.events.failure.get()
    }
    pub fn state(&self) -> i32 {
        // SAFETY: this thread-confined driver retains its native stream.
        unsafe { f::pw_stream_get_state(self.stream(), ptr::null_mut()) }
    }
    pub fn node_id(&self) -> Option<u32> {
        // SAFETY: scalar query of this retained native stream.
        let id = unsafe { f::pw_stream_get_node_id(self.stream()) };
        (id != u32::MAX).then_some(id)
    }
    pub fn offer(&self, pod: &Pod) -> Result<()> {
        self.offer_many(std::slice::from_ref(pod))
    }
    pub fn offer_many(&self, pods: &[Pod]) -> Result<()> {
        if pods.is_empty() || pods.len() > 8 {
            return Err("Invalid offer parameter count");
        }
        let mut params = [ptr::null(); 8];
        for (target, pod) in params.iter_mut().zip(pods) {
            *target = pod.as_ptr();
        }
        let params = &mut params[..pods.len()];
        if self.state() == f::DPE_STREAM_UNCONNECTED {
            // SAFETY: immutable aligned offer is copied during this synchronous
            // call. Fixed flags explicitly exclude RT_PROCESS/cross-thread callbacks.
            let result = unsafe {
                f::pw_stream_connect(
                    self.stream(),
                    f::DPE_STREAM_OUTPUT,
                    u32::MAX,
                    f::DPE_STREAM_DRIVER | f::DPE_STREAM_ALLOC,
                    params.as_mut_ptr(),
                    params.len() as u32,
                )
            };
            if result < 0 {
                Err("PipeWire offer rejected")
            } else {
                Ok(())
            }
        } else {
            self.update(params)
        }
    }
    fn update(&self, params: &mut [*const f::spa_pod]) -> Result<()> {
        // SAFETY: every pointer borrows an owned immutable aligned POD through
        // update_params. No Rust model borrow spans native callback reentry.
        if unsafe {
            f::pw_stream_update_params(self.stream(), params.as_mut_ptr(), params.len() as u32)
        } < 0
        {
            Err("PipeWire parameters rejected")
        } else {
            Ok(())
        }
    }
    pub fn buffers(&self, pods: &[Pod]) -> Result<()> {
        if pods.is_empty() || pods.len() > 4 {
            return Err("Invalid buffer parameter count");
        }
        let mut params = [ptr::null(); 4];
        for (output, pod) in params.iter_mut().zip(pods) {
            *output = pod.as_ptr();
        }
        self.update(&mut params[..pods.len()])
    }
    pub fn trigger(&self) -> Result<()> {
        // SAFETY: the live driver stream supports explicit producer triggering.
        if unsafe { f::pw_stream_trigger_process(self.stream()) } < 0 {
            Err("PipeWire trigger failed")
        } else {
            Ok(())
        }
    }
    pub fn driving(&self) -> bool {
        // SAFETY: scalar query on this retained thread-confined stream.
        unsafe { f::pw_stream_is_driving(self.stream()) }
    }
    pub fn lazy(&self) -> bool {
        // SAFETY: scalar query on this retained thread-confined stream.
        unsafe { f::pw_stream_is_lazy(self.stream()) }
    }
    pub fn dequeue(&self) -> Result<Option<Buffer<T>>> {
        // SAFETY: no RT_PROCESS flag; dequeue/queue is serialized on this loop.
        let pointer = unsafe { f::pw_stream_dequeue_buffer(self.stream()) };
        if pointer.is_null() {
            return Ok(None);
        }
        self.events.checkout(pointer).map(Some)
    }
    pub fn queue(&self, buffer: &Buffer<T>) -> Result<()> {
        self.return_or_queue(buffer, false)
    }
    pub fn return_unused(&self, buffer: &Buffer<T>) -> Result<()> {
        self.return_or_queue(buffer, true)
    }
    fn return_or_queue(&self, buffer: &Buffer<T>, unused: bool) -> Result<()> {
        let pointer = self.events.returning(buffer)?;
        // SAFETY: this exact live buffer belongs to this stream. No RefCell
        // borrow remains during the native call or its synchronous callbacks.
        let result = unsafe {
            if unused {
                f::pw_stream_return_buffer(self.stream(), pointer)
            } else {
                f::pw_stream_queue_buffer(self.stream(), pointer)
            }
        };
        if result < 0 {
            self.events.fail("PipeWire buffer return failed");
            Err("PipeWire buffer return failed")
        } else {
            Ok(())
        }
    }
}
impl<T: 'static> Drop for Driver<T> {
    fn drop(&mut self) {
        self.events.stream.set(ptr::null_mut());
        self.native.close_stream();
        self.events.invalidate();
        // Native is declared before Events, so context teardown also completes
        // before the last listener owner drops. Buffer tokens retain no native
        // allocation after invalidation; GPU owners have separate completion.
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn admission_counts_retired_storage_until_its_last_lease_drops() {
        let drops = Rc::new(Cell::new(0));
        let counted = drops.clone();
        let state = Events::new(1, Box::new(move |_, _| Ok(Storage(counted.clone()))));
        let mut spa = f::spa_buffer::default();
        let mut native = f::pw_buffer {
            buffer: &mut spa,
            ..Default::default()
        };
        let data = (&raw const state).cast_mut().cast();
        // SAFETY: complete mock records; the factory retains only counted Rust
        // storage. Pointer reuse cannot override the still-retained old owner.
        unsafe {
            added::<Storage>(data, &mut native);
        }
        let Some(Event::Added(held)) = state.pop() else {
            panic!("missing buffer");
        };
        // SAFETY: the same native buffer is removed while its lease is retained.
        unsafe {
            removed::<Storage>(data, &mut native);
        }
        assert!(matches!(state.pop(), Some(Event::Removed(1))));
        assert_eq!(state.resident.get(), 1);
        // SAFETY: reuse is rejected before invoking the factory or publishing
        // another storage owner, despite an empty live-pointer registry.
        unsafe {
            added::<Storage>(data, &mut native);
        }
        assert!(matches!(
            state.pop(),
            Some(Event::Failed("PipeWire buffer admission failed"))
        ));
        assert_eq!(drops.get(), 0);
        drop(held);
        assert_eq!(state.resident.get(), 0);
        assert_eq!(drops.get(), 1);
    }
    #[test]
    #[expect(
        unused_assignments,
        reason = "The mocked native records are read through stored raw ABI pointers"
    )]
    fn header_writes_require_live_checkout_and_complete_aligned_metadata() {
        let mut header = std::mem::MaybeUninit::<f::spa_meta_header>::uninit();
        let mut meta = f::spa_meta {
            type_: f::DPE_POD_HEADER,
            size: size_of::<f::spa_meta_header>() as u32,
            data: header.as_mut_ptr().cast(),
        };
        let mut spa = f::spa_buffer {
            n_metas: 1,
            metas: &mut meta,
            ..Default::default()
        };
        let mut native = f::pw_buffer {
            buffer: &mut spa,
            ..Default::default()
        };
        let state = Events::new(1, Box::new(|_, _| Ok(())));
        // SAFETY: these complete mock native records outlive the listener; the
        // factory stores no foreign references and no native code is dispatched.
        unsafe {
            added::<()>((&raw const state).cast_mut().cast(), &mut native);
        }
        let Some(Event::Added(buffer)) = state.pop() else {
            panic!("missing buffer");
        };
        assert!(buffer.set_header(23, 7).is_err());
        let buffer = state.checkout(&mut native).unwrap();
        assert_eq!(buffer.set_header(23, 7), Ok(true));
        // SAFETY: the successful write above initialized the complete header.
        let written = unsafe { header.assume_init() };
        assert_eq!((written.pts, written.seq, written.flags), (23, 7, 0));
        meta.size -= 1;
        assert!(buffer.set_header(24, 8).is_err());
        meta.size += 1;
        meta.data = meta.data.wrapping_byte_add(1);
        assert!(buffer.set_header(24, 8).is_err());
        spa.n_metas = 65;
        assert!(buffer.set_header(24, 8).is_err());
        spa.n_metas = 0;
        assert_eq!(buffer.set_header(24, 8), Ok(false));
        // SAFETY: the exact live listener/native record is now retired.
        unsafe {
            removed::<()>((&raw const state).cast_mut().cast(), &mut native);
        }
        assert!(buffer.set_header(25, 9).is_err());
    }
    #[test]
    fn relay_presence_validation_is_atomic_and_never_writes_timeline_chunks() {
        let mut chunks = [f::spa_chunk {
            size: 0,
            ..Default::default()
        }; 2];
        let mut data = [f::spa_data::default(); 4];
        for index in 0..2 {
            data[index].type_ = f::spa_data_type_SPA_DATA_DmaBuf;
            data[index].chunk = &mut chunks[index];
        }
        data[2].type_ = f::DPE_RELAY_DATA;
        data[3].type_ = f::DPE_RELAY_DATA;
        let mut spa = f::spa_buffer {
            n_datas: 4,
            datas: data.as_mut_ptr(),
            ..Default::default()
        };
        let mut native = f::pw_buffer {
            buffer: &mut spa,
            ..Default::default()
        };
        let state = Events::new(1, Box::new(|_, _| Ok(())));
        // SAFETY: all bounded mock native records remain live throughout these
        // serialized listener calls; no native dispatch or GPU work occurs.
        unsafe { added::<()>((&raw const state).cast_mut().cast(), &mut native) };
        let Some(Event::Added(buffer)) = state.pop() else {
            panic!("missing buffer")
        };
        assert!(buffer.set_relay_ready(2).is_err());
        let buffer = state.checkout(&mut native).unwrap();
        data[1].chunk = ptr::null_mut();
        assert!(buffer.set_relay_ready(2).is_err());
        assert_eq!(chunks[0].size, 0);
        data[1].chunk = &mut chunks[0];
        assert!(buffer.set_relay_ready(2).is_err());
        assert_eq!(chunks[0].size, 0);
        data[1].chunk = &mut chunks[1];
        assert_eq!(buffer.set_relay_ready(2), Ok(()));
        assert_eq!((chunks[0].size, chunks[1].size), (1, 1));
        assert!(data[2].chunk.is_null());
        state.returning(&buffer).unwrap();
        assert!(buffer.set_relay_ready(2).is_err());
        // SAFETY: mock listener pointer and records are still live here.
        unsafe { removed::<()>((&raw const state).cast_mut().cast(), &mut native) };
        assert!(buffer.set_relay_ready(2).is_err());
    }
    struct Storage(Rc<Cell<usize>>);
    impl Drop for Storage {
        fn drop(&mut self) {
            self.0.set(self.0.get() + 1);
        }
    }
    #[test]
    fn removal_reuse_and_driver_retirement_preserve_separate_storage_owners() {
        let drops = Rc::new(Cell::new(0));
        let counted = drops.clone();
        let state = Rc::new(Events::new(
            2,
            Box::new(move |_, _| Ok(Storage(counted.clone()))),
        ));
        let mut spa = f::spa_buffer::default();
        let mut native = f::pw_buffer {
            buffer: &mut spa,
            ..Default::default()
        };
        let data = Rc::as_ptr(&state).cast_mut().cast();
        // SAFETY: these complete synthetic native records outlive every callback;
        // the factory owns only a counted Rust resource and no foreign reference.
        unsafe {
            added::<Storage>(data, &mut native);
        }
        let Some(Event::Added(first)) = state.pop() else {
            panic!("missing first buffer")
        };
        assert_eq!(first.serial(), 1);
        assert!(first.live());
        // SAFETY: removal borrows the same valid listener and native record.
        unsafe {
            removed::<Storage>(data, &mut native);
        }
        assert!(!first.live());
        assert!(matches!(state.pop(), Some(Event::Removed(1))));
        assert_eq!(drops.get(), 0); // Native removal is not downstream GPU release.
        // SAFETY: native pointer reuse is represented by a new add notification.
        unsafe {
            added::<Storage>(data, &mut native);
        }
        let Some(Event::Added(second)) = state.pop() else {
            panic!("missing second buffer")
        };
        assert_eq!(second.serial(), 2);
        assert!(!Rc::ptr_eq(&first.0, &second.0));
        state.invalidate();
        assert!(!second.live());
        drop(state);
        assert_eq!(drops.get(), 0);
        drop(second);
        assert_eq!(drops.get(), 1);
        drop(first);
        assert_eq!(drops.get(), 2);
    }
    #[test]
    fn callback_bursts_coalesce_process_requests_and_bound_other_events() {
        let state = Events::new(1, Box::new(|_, _| Ok(())));
        let data = (&raw const state).cast_mut().cast();
        for _ in 0..1024 {
            // SAFETY: this complete listener remains borrowed on this thread.
            unsafe {
                process::<()>(data);
            }
        }
        assert_eq!(state.values.borrow().len(), 1);
        assert!(matches!(state.pop(), Some(Event::Process)));
        assert!(state.pop().is_none());
        for _ in 0..129 {
            // SAFETY: fixed scalar state and null optional error use the native ABI.
            unsafe {
                state_changed::<()>(data, 1, 2, ptr::null());
            }
        }
        assert!(matches!(
            state.pop(),
            Some(Event::Failed("PipeWire event limit"))
        ));
        assert_eq!(state.values.borrow().len(), 128);
    }
    #[test]
    fn failed_and_panicking_factories_never_publish_a_usable_buffer() {
        for panic in [false, true] {
            let state = Events::new(
                1,
                Box::new(move |_, _| -> Result<()> {
                    assert!(!panic, "synthetic buffer factory panic");
                    Err("synthetic allocation failure")
                }),
            );
            let mut spa = f::spa_buffer::default();
            let mut native = f::pw_buffer {
                buffer: &mut spa,
                ..Default::default()
            };
            // SAFETY: only the complete synthetic buffer/listener records are borrowed.
            unsafe {
                added::<()>((&raw const state).cast_mut().cast(), &mut native);
            }
            assert!(matches!(state.pop(), Some(Event::Failed(_))));
            assert!(state.buffers.borrow().is_empty());
            assert!(state.pop().is_none());
        }
    }
    #[test]
    fn checkout_and_return_reject_duplicate_foreign_and_retired_buffer_tokens() {
        let state = Events::new(2, Box::new(|_, _| Ok(())));
        let foreign = Events::new(2, Box::new(|_, _| Ok(())));
        let mut spa = f::spa_buffer::default();
        let mut native = f::pw_buffer {
            buffer: &mut spa,
            ..Default::default()
        };
        // SAFETY: complete synthetic records outlive these callbacks; neither
        // factory writes memory, and both have independent Rust registries.
        unsafe {
            added::<()>((&raw const state).cast_mut().cast(), &mut native);
            added::<()>((&raw const foreign).cast_mut().cast(), &mut native);
        }
        let Some(Event::Added(owner)) = state.pop() else {
            panic!("missing buffer")
        };
        assert!(state.returning(&owner).is_err());
        let checked = state.checkout(&mut native).unwrap();
        assert!(foreign.returning(&checked).is_err());
        assert_eq!(state.returning(&checked).unwrap(), &mut native as *mut _);
        assert!(state.returning(&checked).is_err());
        let checked = state.checkout(&mut native).unwrap();
        // SAFETY: the same valid native record is now removed from this registry.
        unsafe {
            removed::<()>((&raw const state).cast_mut().cast(), &mut native);
        }
        assert!(state.returning(&checked).is_err());
        assert!(state.checkout(&mut native).is_err());
    }
    #[test]
    fn synchronous_buffer_factory_can_publish_an_unrelated_event_without_model_borrows() {
        let slot = Rc::new(RefCell::new(std::rc::Weak::<Events<()>>::new()));
        let weak = slot.clone();
        let state = Rc::new(Events::new(
            1,
            Box::new(move |_, _| {
                let state = weak.borrow().upgrade().unwrap();
                // SAFETY: this mock reentry only publishes a scalar process event;
                // it neither dispatches nor mutates the native stream/buffer.
                unsafe {
                    process::<()>(Rc::as_ptr(&state).cast_mut().cast());
                }
                Ok(())
            }),
        ));
        *slot.borrow_mut() = Rc::downgrade(&state);
        let mut spa = f::spa_buffer::default();
        let mut native = f::pw_buffer {
            buffer: &mut spa,
            ..Default::default()
        };
        // SAFETY: the complete synthetic records remain borrowed on this thread.
        unsafe {
            added::<()>(Rc::as_ptr(&state).cast_mut().cast(), &mut native);
        }
        assert!(state.failure.get().is_none());
        assert!(matches!(state.pop(), Some(Event::Process)));
        assert!(matches!(state.pop(), Some(Event::Added(_))));
    }
}
