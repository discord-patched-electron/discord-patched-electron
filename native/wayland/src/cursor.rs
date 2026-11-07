// SPDX-License-Identifier: MIT
use dpe_core::handles::Handles;
use std::{
    ffi::{CStr, c_void},
    fs::File,
    io,
    os::{
        fd::{AsFd, AsRawFd, FromRawFd},
        unix::fs::FileExt,
    },
    ptr,
    sync::{Arc, Mutex, OnceLock},
    thread::{self, ThreadId},
};
use wayland_client::{
    Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum, delegate_noop,
    event_created_child,
    protocol::{wl_buffer, wl_pointer, wl_registry, wl_seat, wl_shm, wl_shm_pool},
};
use wayland_protocols::ext::{
    foreign_toplevel_list::v1::client::{
        ext_foreign_toplevel_handle_v1 as top, ext_foreign_toplevel_list_v1 as list,
    },
    image_capture_source::v1::client::{
        ext_foreign_toplevel_image_capture_source_manager_v1 as sources,
        ext_image_capture_source_v1 as source,
    },
    image_copy_capture::v1::client::{
        ext_image_copy_capture_cursor_session_v1 as cursor,
        ext_image_copy_capture_frame_v1 as frame, ext_image_copy_capture_manager_v1 as manager,
        ext_image_copy_capture_session_v1 as session,
    },
};
const EDGE: u32 = 256;
struct Storage {
    buffer: wl_buffer::WlBuffer,
    file: File,
    width: u32,
    height: u32,
    generation: u64,
}
impl Drop for Storage {
    fn drop(&mut self) {
        self.buffer.destroy();
    }
}
struct State {
    identifier: String,
    shm: Option<wl_shm::WlShm>,
    seat: Option<wl_seat::WlSeat>,
    pointer: Option<wl_pointer::WlPointer>,
    manager: Option<manager::ExtImageCopyCaptureManagerV1>,
    sources: Option<sources::ExtForeignToplevelImageCaptureSourceManagerV1>,
    list: Option<list::ExtForeignToplevelListV1>,
    target: Option<top::ExtForeignToplevelHandleV1>,
    source: Option<source::ExtImageCaptureSourceV1>,
    cursor: Option<cursor::ExtImageCopyCaptureCursorSessionV1>,
    session: Option<session::ExtImageCopyCaptureSessionV1>,
    main: Option<session::ExtImageCopyCaptureSessionV1>,
    frame: Option<frame::ExtImageCopyCaptureFrameV1>,
    storage: Option<Storage>,
    // Word alignment preserves the SPA C layout. Allocate only actual cursor
    // pixels after its first completed image, rather than 256 KiB at startup.
    metadata: Vec<u32>,
    next_size: (u32, u32),
    main_size: (u32, u32),
    position: (i32, i32),
    hotspot: (i32, i32),
    generation: u64,
    frame_generation: u64,
    serial: u32,
    announced: usize,
    entered: bool,
    valid: bool,
    main_valid: bool,
    stopped: bool,
    argb: bool,
}
impl State {
    fn new(identifier: String) -> Self {
        Self {
            identifier,
            shm: None,
            seat: None,
            pointer: None,
            manager: None,
            sources: None,
            list: None,
            target: None,
            source: None,
            cursor: None,
            session: None,
            main: None,
            frame: None,
            storage: None,
            metadata: vec![0; 12],
            next_size: (0, 0),
            main_size: (0, 0),
            position: (0, 0),
            hotspot: (0, 0),
            generation: 0,
            frame_generation: 0,
            serial: 0,
            announced: 0,
            entered: false,
            valid: false,
            main_valid: false,
            stopped: false,
            argb: false,
        }
    }
    fn stop(&mut self) {
        self.valid = false;
        self.stopped = true;
    }
    fn start(&mut self, qh: &QueueHandle<Self>) {
        if self.stopped || self.source.is_some() {
            return;
        }
        let (Some(target), Some(pointer), Some(manager), Some(sources), Some(_)) = (
            &self.target,
            &self.pointer,
            &self.manager,
            &self.sources,
            &self.shm,
        ) else {
            return;
        };
        let source = sources.create_source(target, qh, ());
        self.main = Some(manager.create_session(&source, manager::Options::empty(), qh, true));
        let cursor = manager.create_pointer_cursor_session(&source, pointer, qh, ());
        self.session = Some(cursor.get_capture_session(qh, false));
        self.cursor = Some(cursor);
        self.source = Some(source);
    }
    fn allocate(&mut self, qh: &QueueHandle<Self>) -> io::Result<()> {
        let (width, height) = self.next_size;
        if width == 0 || height == 0 || width > EDGE || height > EDGE {
            return Err(io::Error::from_raw_os_error(libc::EINVAL));
        }
        let bytes = width * height * 4;
        // SAFETY: fixed valid memfd name/flags; a successful return transfers one fd.
        let raw = unsafe {
            libc::memfd_create(
                c"dpe-cursor".as_ptr(),
                libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING,
            )
        };
        if raw < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: memfd_create returned a uniquely owned descriptor.
        let file = unsafe { File::from_raw_fd(raw) };
        file.set_len(u64::from(bytes))?;
        // SAFETY: only size seals are applied; compositor retains write access.
        if unsafe {
            libc::fcntl(
                file.as_raw_fd(),
                libc::F_ADD_SEALS,
                libc::F_SEAL_SHRINK | libc::F_SEAL_GROW | libc::F_SEAL_SEAL,
            )
        } < 0
        {
            return Err(io::Error::last_os_error());
        }
        let shm = self
            .shm
            .as_ref()
            .ok_or_else(|| io::Error::from_raw_os_error(libc::ENOTCONN))?;
        let pool = shm.create_pool(file.as_fd(), bytes as i32, qh, ());
        let buffer = pool.create_buffer(
            0,
            width as i32,
            height as i32,
            (width * 4) as i32,
            wl_shm::Format::Argb8888,
            qh,
            (),
        );
        pool.destroy();
        self.storage = Some(Storage {
            buffer,
            file,
            width,
            height,
            generation: self.generation,
        });
        Ok(())
    }
    fn submit(&mut self, qh: &QueueHandle<Self>) {
        if self.stopped || self.frame.is_some() {
            return;
        }
        let (Some(storage), Some(session)) = (&self.storage, &self.session) else {
            return;
        };
        self.frame_generation = storage.generation;
        let frame = session.create_frame(qh, ());
        frame.attach_buffer(&storage.buffer);
        frame.damage_buffer(0, 0, storage.width as i32, storage.height as i32);
        frame.capture();
        self.frame = Some(frame);
    }
    fn commit(&mut self) -> io::Result<()> {
        let storage = self
            .storage
            .as_ref()
            .ok_or_else(|| io::Error::from_raw_os_error(libc::EINVAL))?;
        let bytes = storage.width as usize * storage.height as usize * 4;
        // No shared Rust slice aliases compositor writes. pread copies only the
        // completed bounded cursor into owned bytes after the ready event.
        self.metadata.resize(12 + bytes / 4, 0);
        // SAFETY: the exclusively borrowed initialized u32 words cover exactly
        // bytes pixels; every byte pattern is a valid u32. No word references
        // or C metadata pointer remain borrowed across this call.
        let pixels = unsafe {
            std::slice::from_raw_parts_mut(self.metadata[12..].as_mut_ptr().cast::<u8>(), bytes)
        };
        storage.file.read_exact_at(pixels, 0)?;
        self.serial = self.serial.wrapping_add(1).max(1);
        self.metadata[..12].copy_from_slice(&[
            self.serial,
            0,
            0,
            0,
            self.hotspot.0 as u32,
            self.hotspot.1 as u32,
            28,
            12,
            storage.width,
            storage.height,
            storage.width * 4,
            20,
        ]);
        self.valid = true;
        Ok(())
    }
    fn metadata(&mut self, width: u32, height: u32) -> *const c_void {
        if self.stopped
            || !self.valid
            || !self.entered
            || !self.main_valid
            || self.main_size != (width, height)
        {
            return ptr::null();
        }
        self.metadata[2] = self.position.0 as u32;
        self.metadata[3] = self.position.1 as u32;
        self.metadata.as_ptr().cast()
    }
}
impl Dispatch<wl_registry::WlRegistry, ()> for State {
    fn event(
        state: &mut Self,
        registry: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if state.stopped {
            return;
        }
        match event {
            wl_registry::Event::Global {
                name,
                interface,
                version,
            } if version >= 1 => {
                match interface.as_str() {
                    "wl_shm" if state.shm.is_none() => {
                        state.shm = Some(registry.bind(name, 1, qh, ()))
                    }
                    "wl_seat" => {
                        if state.seat.is_some() {
                            state.stop();
                            return;
                        }
                        state.seat = Some(registry.bind(name, 1, qh, ()));
                    }
                    "ext_foreign_toplevel_list_v1" if state.list.is_none() => {
                        state.list = Some(registry.bind(name, 1, qh, ()))
                    }
                    "ext_foreign_toplevel_image_capture_source_manager_v1"
                        if state.sources.is_none() =>
                    {
                        state.sources = Some(registry.bind(name, 1, qh, ()))
                    }
                    "ext_image_copy_capture_manager_v1" if state.manager.is_none() => {
                        state.manager = Some(registry.bind(name, 1, qh, ()))
                    }
                    _ => {}
                }
                state.start(qh);
            }
            wl_registry::Event::GlobalRemove { .. } => state.stop(),
            _ => {}
        }
    }
}
impl Dispatch<wl_seat::WlSeat, ()> for State {
    fn event(
        state: &mut Self,
        seat: &wl_seat::WlSeat,
        event: wl_seat::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if state.stopped {
            return;
        }
        if let wl_seat::Event::Capabilities { capabilities } = event {
            match capabilities {
                WEnum::Value(value) if value.contains(wl_seat::Capability::Pointer) => {
                    if state.pointer.is_none() {
                        state.pointer = Some(seat.get_pointer(qh, ()));
                        state.start(qh);
                    }
                }
                _ => {
                    if state.pointer.is_some() {
                        state.stop();
                    }
                }
            }
        }
    }
}
impl Dispatch<list::ExtForeignToplevelListV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &list::ExtForeignToplevelListV1,
        event: list::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            list::Event::Toplevel { toplevel } => {
                state.announced += 1;
                if state.stopped || state.announced > 1024 {
                    toplevel.destroy();
                    state.stop();
                }
            }
            list::Event::Finished => state.stop(),
            _ => {}
        }
    }
    event_created_child!(State,list::ExtForeignToplevelListV1,[0=>(top::ExtForeignToplevelHandleV1,())]);
}
impl Dispatch<top::ExtForeignToplevelHandleV1, ()> for State {
    fn event(
        state: &mut Self,
        top: &top::ExtForeignToplevelHandleV1,
        event: top::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            top::Event::Identifier { identifier } => {
                if !state.stopped && identifier == state.identifier {
                    if state.target.as_ref().is_some_and(|target| target != top) {
                        state.stop();
                        top.destroy();
                        return;
                    }
                    state.target = Some(top.clone());
                    state.start(qh);
                } else {
                    top.destroy();
                }
            }
            top::Event::Closed => {
                if state.target.as_ref() == Some(top) {
                    state.stop();
                } else if top.is_alive() {
                    top.destroy();
                }
            }
            _ => {}
        }
    }
}
impl Dispatch<cursor::ExtImageCopyCaptureCursorSessionV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &cursor::ExtImageCopyCaptureCursorSessionV1,
        event: cursor::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if state.stopped {
            return;
        }
        match event {
            cursor::Event::Enter => state.entered = true,
            cursor::Event::Leave => state.entered = false,
            cursor::Event::Position { x, y } => state.position = (x, y),
            cursor::Event::Hotspot { x, y } => state.hotspot = (x, y),
            _ => {}
        }
    }
}
impl Dispatch<session::ExtImageCopyCaptureSessionV1, bool> for State {
    fn event(
        state: &mut Self,
        _: &session::ExtImageCopyCaptureSessionV1,
        event: session::Event,
        main: &bool,
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if state.stopped {
            return;
        }
        match event {
            session::Event::BufferSize { width, height } => {
                if *main {
                    state.main_valid = false;
                    state.main_size = (width, height);
                } else {
                    state.valid = false;
                    state.next_size = (width, height);
                }
            }
            session::Event::ShmFormat { format }
                if !*main && format == WEnum::Value(wl_shm::Format::Argb8888) =>
            {
                state.argb = true
            }
            session::Event::Done if *main => {
                if state.main_size.0 == 0
                    || state.main_size.1 == 0
                    || state.main_size.0 > 16384
                    || state.main_size.1 > 16384
                {
                    state.stop();
                } else {
                    state.main_valid = true;
                }
            }
            session::Event::Done => {
                let (width, height) = state.next_size;
                if !state.argb || width == 0 || height == 0 || width > EDGE || height > EDGE {
                    state.stop();
                    return;
                }
                state.argb = false;
                state.valid = false;
                let Some(generation) = state.generation.checked_add(1) else {
                    state.stop();
                    return;
                };
                state.generation = generation;
                if state.frame.is_none() {
                    if state.allocate(qh).is_err() {
                        state.stop();
                    } else {
                        state.submit(qh);
                    }
                }
            }
            session::Event::Stopped => state.stop(),
            _ => {}
        }
    }
}
impl Dispatch<frame::ExtImageCopyCaptureFrameV1, ()> for State {
    fn event(
        state: &mut Self,
        frame: &frame::ExtImageCopyCaptureFrameV1,
        event: frame::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            frame::Event::Transform { transform } => {
                if transform != WEnum::Value(wayland_client::protocol::wl_output::Transform::Normal)
                {
                    state.stop();
                }
            }
            frame::Event::Ready | frame::Event::Failed { .. } => {
                frame.destroy();
                state.frame = None;
                if state.stopped {
                    return;
                }
                let changed = state.frame_generation != state.generation;
                let valid = match event {
                    frame::Event::Ready => true,
                    frame::Event::Failed { reason } => {
                        changed && reason == WEnum::Value(frame::FailureReason::BufferConstraints)
                    }
                    _ => false,
                };
                if !valid
                    || (changed && state.allocate(qh).is_err())
                    || (!changed && state.commit().is_err())
                {
                    state.stop();
                    return;
                }
                state.submit(qh);
            }
            _ => {}
        }
    }
}
delegate_noop!(State: ignore wl_pointer::WlPointer);
delegate_noop!(State: ignore wl_shm::WlShm);
delegate_noop!(State: ignore wl_shm_pool::WlShmPool);
delegate_noop!(State: ignore wl_buffer::WlBuffer);
delegate_noop!(State: ignore manager::ExtImageCopyCaptureManagerV1);
delegate_noop!(State: ignore sources::ExtForeignToplevelImageCaptureSourceManagerV1);
delegate_noop!(State: ignore source::ExtImageCaptureSourceV1);

struct Client {
    queue: EventQueue<State>,
    state: State,
    owner: ThreadId,
}
impl Client {
    fn new(identifier: String) -> Option<Self> {
        let connection = Connection::connect_to_env().ok()?;
        let queue = connection.new_event_queue::<State>();
        connection.display().get_registry(&queue.handle(), ());
        Some(Self {
            queue,
            state: State::new(identifier),
            owner: thread::current().id(),
        })
    }
    fn pump(&mut self) {
        if self.state.stopped {
            return;
        }
        for _ in 0..4 {
            if self.queue.dispatch_pending(&mut self.state).is_err() {
                self.state.stop();
                return;
            }
            if self.state.stopped {
                return;
            }
            if let Err(error) = self.queue.flush()
                && !matches!(error,wayland_client::backend::WaylandError::Io(ref error) if matches!(error.kind(),io::ErrorKind::WouldBlock|io::ErrorKind::Interrupted))
            {
                self.state.stop();
                return;
            }
            let Some(guard) = self.queue.prepare_read() else {
                continue;
            };
            let mut poll = libc::pollfd {
                fd: guard.connection_fd().as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            // SAFETY: one initialized pollfd, zero timeout; capture never waits for a cursor.
            let status = unsafe { libc::poll(&mut poll, 1, 0) };
            if status < 0 {
                if io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
                    self.state.stop();
                }
                return;
            }
            if status == 0 {
                return;
            }
            if poll.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
                self.state.stop();
                return;
            }
            if let Err(error) = guard.read()
                && !matches!(error,wayland_client::backend::WaylandError::Io(ref error) if matches!(error.kind(),io::ErrorKind::WouldBlock|io::ErrorKind::Interrupted))
            {
                self.state.stop();
                return;
            }
        }
        if self.queue.dispatch_pending(&mut self.state).is_err() {
            self.state.stop();
        }
    }
}
impl Drop for Client {
    fn drop(&mut self) {
        self.state.stop();
        if let Some(value) = self.state.frame.take() {
            value.destroy();
        }
        if let Some(value) = self.state.session.take() {
            value.destroy();
        }
        if let Some(value) = self.state.cursor.take() {
            value.destroy();
        }
        if let Some(value) = self.state.main.take() {
            value.destroy();
        }
        if let Some(value) = self.state.source.take() {
            value.destroy();
        }
        self.state.storage = None;
        if let Some(value) = self.state.target.take()
            && value.is_alive()
        {
            value.destroy();
        }
        if let Some(value) = self.state.list.take() {
            value.destroy();
        }
        if let Some(value) = self.state.manager.take() {
            value.destroy();
        }
        if let Some(value) = self.state.sources.take() {
            value.destroy();
        }
        let _ = self.queue.flush();
    }
}
fn clients() -> &'static Handles<Arc<Mutex<Client>>> {
    static TABLE: OnceLock<Handles<Arc<Mutex<Client>>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::new(16))
}
fn lock<T>(value: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    value
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
/// # Safety
/// Identifier is a NUL-terminated string. All calls and consumption of returned
/// metadata occur on the creating thread; metadata lasts until its next call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_wl_cursor_create(identifier: *const libc::c_char) -> *mut c_void {
    if identifier.is_null() {
        return ptr::null_mut();
    }
    // SAFETY: caller borrows a valid C string for this call.
    let identifier = unsafe { CStr::from_ptr(identifier) }.to_bytes();
    if identifier.is_empty() || identifier.len() > 32 {
        return ptr::null_mut();
    }
    let Ok(identifier) = std::str::from_utf8(identifier) else {
        return ptr::null_mut();
    };
    let Some(client) = Client::new(identifier.to_owned()) else {
        return ptr::null_mut();
    };
    clients()
        .insert(Arc::new(Mutex::new(client)))
        .map_or(ptr::null_mut(), |token| token.get() as *mut c_void)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_wl_cursor_metadata(
    handle: *mut c_void,
    width: u32,
    height: u32,
) -> *const c_void {
    with_metadata(handle as usize, width, height, |words| {
        words.as_ptr().cast()
    })
    .unwrap_or(ptr::null())
}
/// Consume aligned SPA cursor words with the client owner and queue locked.
/// The callback must not reenter this cursor. Its borrowed words cannot escape.
pub fn with_metadata<R>(
    handle: usize,
    width: u32,
    height: u32,
    consume: impl FnOnce(&[u32]) -> R,
) -> Option<R> {
    let client = clients().get(handle)?;
    let mut client = lock(&client);
    if client.owner != thread::current().id() {
        return None;
    }
    client.pump();
    if client.state.metadata(width, height).is_null() {
        None
    } else {
        Some(consume(&client.state.metadata))
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_wl_cursor_destroy(handle: *mut c_void) {
    let Some(client) = clients().get(handle as usize) else {
        return;
    };
    if lock(&client).owner == thread::current().id() {
        drop(clients().remove(handle as usize));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::net::UnixStream;
    // An unserved private connection permits constructing typed proxy requests;
    // no compositor, host socket, output, cursor or desktop image is accessed.
    fn fixture() -> (State, EventQueue<State>, UnixStream) {
        let (socket, peer) = UnixStream::pair().unwrap();
        let connection = Connection::from_socket(socket).unwrap();
        let queue = connection.new_event_queue::<State>();
        let registry = connection.display().get_registry(&queue.handle(), ());
        let mut state = State::new("synthetic-game".into());
        state.shm = Some(registry.bind(1, 1, &queue.handle(), ()));
        state.next_size = (2, 1);
        state.main_size = (16, 16);
        state.generation = 1;
        state.allocate(&queue.handle()).unwrap();
        (state, queue, peer)
    }
    #[test]
    fn owned_cursor_pixels_hotspots_and_coordinate_space_remain_atomic() {
        let (mut state, _queue, _peer) = fixture();
        let pixels = [0u8, 0, 128, 128, 0, 255, 0, 255];
        state
            .storage
            .as_ref()
            .unwrap()
            .file
            .write_all_at(&pixels, 0)
            .unwrap();
        state.hotspot = (1, 0);
        state.position = (5, 6);
        state.entered = true;
        state.main_valid = true;
        assert!(state.metadata(16, 16).is_null());
        state.commit().unwrap();
        assert!(!state.metadata(16, 16).is_null());
        assert_eq!(
            &state.metadata[..12],
            &[1, 0, 5, 6, 1, 0, 28, 12, 2, 1, 8, 20]
        );
        assert_eq!(
            &state.metadata[12..],
            &[
                u32::from_ne_bytes(pixels[..4].try_into().unwrap()),
                u32::from_ne_bytes(pixels[4..].try_into().unwrap())
            ]
        );
        assert_eq!(state.metadata.len() * 4, 48 + pixels.len());
        state.hotspot = (0, 1);
        state.position = (-1, 16);
        state.metadata(16, 16);
        assert_eq!(&state.metadata[4..6], &[1, 0]);
        assert_eq!(state.metadata[2], u32::MAX);
        state.commit().unwrap();
        assert_eq!(&state.metadata[4..6], &[0, 1]);
        assert_eq!(state.metadata[0], 2);
        state.entered = false;
        assert!(state.metadata(16, 16).is_null());
        state.entered = true;
        assert!(!state.metadata(16, 16).is_null());
        assert!(state.metadata(32, 16).is_null());
        state.main_size = (32, 16);
        state.main_valid = false;
        assert!(state.metadata(32, 16).is_null());
        state.main_valid = true;
        assert!(!state.metadata(32, 16).is_null());
        state.stop();
        assert!(state.metadata(32, 16).is_null());
    }
    #[test]
    fn allocation_bounds_and_invalid_handles_fail_without_foreign_access() {
        let (mut state, queue, _peer) = fixture();
        state.next_size = (257, 1);
        assert!(state.allocate(&queue.handle()).is_err());
        assert_eq!(state.storage.as_ref().unwrap().width, 2);
        state.next_size = (1, 0);
        assert!(state.allocate(&queue.handle()).is_err());
        assert!(dpe_wl_cursor_metadata(ptr::null_mut(), 16, 16).is_null());
        dpe_wl_cursor_destroy(ptr::null_mut());
        // SAFETY: these inputs are either null or valid, empty/oversized C strings;
        // they are rejected before connecting to any Wayland socket.
        unsafe {
            assert!(dpe_wl_cursor_create(ptr::null()).is_null());
            assert!(dpe_wl_cursor_create(c"".as_ptr()).is_null());
            assert!(dpe_wl_cursor_create(c"123456789012345678901234567890123".as_ptr()).is_null());
        }
    }
}
