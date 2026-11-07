// SPDX-License-Identifier: MIT
//! Optional XCB boundary with scoped replies and event-driven cursor storage.
//! All handles/storage belong to the calling thread. No thread, PipeWire loop,
//! game pixel readback or maximum-sized metadata allocation is introduced.
use dpe_core::handles::Handles;
use std::{
    ffi::{CStr, c_char, c_void},
    ptr::{self, NonNull},
    time::{Duration, Instant},
};
mod ffi {
    #![allow(
        non_upper_case_globals,
        non_camel_case_types,
        non_snake_case,
        dead_code,
        clippy::all,
        clippy::undocumented_unsafe_blocks
    )]
    include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
}
use ffi as f;
type Xcb = *mut f::xcb_connection_t;
struct Library(NonNull<c_void>);
impl Library {
    fn open(name: &CStr) -> Option<Self> {
        // SAFETY: only the fixed system XCB library names are passed here.
        NonNull::new(unsafe { libc::dlopen(name.as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) })
            .map(Self)
    }
    fn symbol(&self, name: &CStr) -> Option<NonNull<c_void>> {
        // SAFETY: this guard pins the library and name is terminated.
        NonNull::new(unsafe { libc::dlsym(self.0.as_ptr(), name.as_ptr()) })
    }
}
impl Drop for Library {
    fn drop(&mut self) {
        // SAFETY: this guard owns exactly one successful dlopen reference.
        unsafe { libc::dlclose(self.0.as_ptr()) };
    }
}
struct Api {
    connect: unsafe extern "C" fn(*const c_char, *mut i32) -> Xcb,
    disconnect: unsafe extern "C" fn(Xcb),
    error: unsafe extern "C" fn(Xcb) -> i32,
    flush: unsafe extern "C" fn(Xcb) -> i32,
    poll_reply:
        unsafe extern "C" fn(Xcb, u32, *mut *mut c_void, *mut *mut f::xcb_generic_error_t) -> i32,
    discard: unsafe extern "C" fn(Xcb, u32),
    event: unsafe extern "C" fn(Xcb) -> *mut f::xcb_generic_event_t,
    pointer: unsafe extern "C" fn(Xcb, u32) -> f::xcb_query_pointer_cookie_t,
    geometry: unsafe extern "C" fn(Xcb, u32) -> f::xcb_get_geometry_cookie_t,
    image: unsafe extern "C" fn(Xcb) -> f::xcb_xfixes_get_cursor_image_cookie_t,
    version: unsafe extern "C" fn(Xcb, u32, u32) -> f::xcb_xfixes_query_version_cookie_t,
    select: unsafe extern "C" fn(Xcb, u32, u32) -> f::xcb_void_cookie_t,
    attributes: unsafe extern "C" fn(Xcb, u32, u32, *const c_void) -> f::xcb_void_cookie_t,
    setup: unsafe extern "C" fn(Xcb) -> *const f::xcb_setup_t,
    roots: unsafe extern "C" fn(*const f::xcb_setup_t) -> f::xcb_screen_iterator_t,
    next: unsafe extern "C" fn(*mut f::xcb_screen_iterator_t),
    extension:
        unsafe extern "C" fn(Xcb, *mut f::xcb_extension_t) -> *const f::xcb_query_extension_reply_t,
    fixes_id: NonNull<c_void>,
    _xcb: Library,
    _fixes: Library,
}
impl Api {
    fn load() -> Option<Self> {
        let xcb = Library::open(c"libxcb.so.1")?;
        let fixes = Library::open(c"libxcb-xfixes.so.0")?;
        macro_rules! load {
            ($library:ident,$name:literal,$ty:ty) => {{
                let pointer = $library.symbol($name)?.as_ptr();
                // SAFETY: each signature is fixed by the system XCB headers.
                unsafe { std::mem::transmute::<*mut c_void, $ty>(pointer) }
            }};
        }
        Some(Self {
            connect: load!(
                xcb,
                c"xcb_connect",
                unsafe extern "C" fn(*const c_char, *mut i32) -> Xcb
            ),
            disconnect: load!(xcb, c"xcb_disconnect", unsafe extern "C" fn(Xcb)),
            error: load!(
                xcb,
                c"xcb_connection_has_error",
                unsafe extern "C" fn(Xcb) -> i32
            ),
            flush: load!(xcb, c"xcb_flush", unsafe extern "C" fn(Xcb) -> i32),
            poll_reply: load!(
                xcb,
                c"xcb_poll_for_reply",
                unsafe extern "C" fn(
                    Xcb,
                    u32,
                    *mut *mut c_void,
                    *mut *mut f::xcb_generic_error_t,
                ) -> i32
            ),
            discard: load!(xcb, c"xcb_discard_reply", unsafe extern "C" fn(Xcb, u32)),
            event: load!(
                xcb,
                c"xcb_poll_for_event",
                unsafe extern "C" fn(Xcb) -> *mut f::xcb_generic_event_t
            ),
            pointer: load!(
                xcb,
                c"xcb_query_pointer",
                unsafe extern "C" fn(Xcb, u32) -> f::xcb_query_pointer_cookie_t
            ),
            geometry: load!(
                xcb,
                c"xcb_get_geometry",
                unsafe extern "C" fn(Xcb, u32) -> f::xcb_get_geometry_cookie_t
            ),
            image: load!(
                fixes,
                c"xcb_xfixes_get_cursor_image",
                unsafe extern "C" fn(Xcb) -> f::xcb_xfixes_get_cursor_image_cookie_t
            ),
            version: load!(
                fixes,
                c"xcb_xfixes_query_version",
                unsafe extern "C" fn(Xcb, u32, u32) -> f::xcb_xfixes_query_version_cookie_t
            ),
            select: load!(
                fixes,
                c"xcb_xfixes_select_cursor_input",
                unsafe extern "C" fn(Xcb, u32, u32) -> f::xcb_void_cookie_t
            ),
            attributes: load!(
                xcb,
                c"xcb_change_window_attributes",
                unsafe extern "C" fn(Xcb, u32, u32, *const c_void) -> f::xcb_void_cookie_t
            ),
            setup: load!(
                xcb,
                c"xcb_get_setup",
                unsafe extern "C" fn(Xcb) -> *const f::xcb_setup_t
            ),
            roots: load!(
                xcb,
                c"xcb_setup_roots_iterator",
                unsafe extern "C" fn(*const f::xcb_setup_t) -> f::xcb_screen_iterator_t
            ),
            next: load!(
                xcb,
                c"xcb_screen_next",
                unsafe extern "C" fn(*mut f::xcb_screen_iterator_t)
            ),
            extension: load!(
                xcb,
                c"xcb_get_extension_data",
                unsafe extern "C" fn(
                    Xcb,
                    *mut f::xcb_extension_t,
                ) -> *const f::xcb_query_extension_reply_t
            ),
            fixes_id: fixes.symbol(c"xcb_xfixes_id")?,
            _xcb: xcb,
            _fixes: fixes,
        })
    }
}
struct Allocation(NonNull<c_void>);
impl Drop for Allocation {
    fn drop(&mut self) {
        // SAFETY: libxcb transfers malloc-owned reply/event/error allocations.
        unsafe { libc::free(self.0.as_ptr()) };
    }
}
impl Allocation {
    unsafe fn read<T: Copy>(&self) -> T {
        // SAFETY: caller selects the fixed POD header matching this XCB reply.
        unsafe { ptr::read(self.0.as_ptr().cast::<T>()) }
    }
}
struct Connection {
    raw: NonNull<f::xcb_connection_t>,
    api: Api,
    root: u32,
    event: u8,
}
impl Drop for Connection {
    fn drop(&mut self) {
        // SAFETY: connection is live and libraries remain owned until this returns.
        unsafe { (self.api.disconnect)(self.raw.as_ptr()) };
    }
}
struct Pending {
    sequence: u32,
    sent: Instant,
    generation: u64,
}
impl Pending {
    fn new(sequence: u32, generation: u64) -> Result<Self, ()> {
        if sequence == 0 {
            return Err(());
        };
        Ok(Self {
            sequence,
            sent: Instant::now(),
            generation,
        })
    }
}
impl Connection {
    fn open() -> Option<Self> {
        std::env::var_os("DISPLAY").filter(|value| !value.is_empty())?;
        let api = Api::load()?;
        let mut screen = 0;
        // SAFETY: XCB reads DISPLAY and writes the selected screen index.
        let raw = NonNull::new(unsafe { (api.connect)(ptr::null(), &raw mut screen) })?;
        let mut connection = Self {
            raw,
            api,
            root: 0,
            event: 0,
        };
        if !(0..=255).contains(&screen) {
            return None;
        };
        // SAFETY: connection owns a fresh XCB reference; setup/extension data
        // stay borrowed until disconnect. Bounded iteration checks each screen.
        unsafe {
            if (connection.api.error)(raw.as_ptr()) != 0 {
                return None;
            };
            let setup = (connection.api.setup)(raw.as_ptr());
            if setup.is_null() {
                return None;
            };
            let mut roots = (connection.api.roots)(setup);
            for _ in 0..screen {
                if roots.rem <= 0 {
                    return None;
                };
                (connection.api.next)(&raw mut roots);
            }
            if roots.rem <= 0 || roots.data.is_null() {
                return None;
            };
            connection.root = (*roots.data).root;
            let extension =
                (connection.api.extension)(raw.as_ptr(), connection.api.fixes_id.as_ptr().cast());
            if extension.is_null() || (*extension).present == 0 || (*extension).first_event > 253 {
                return None;
            };
            connection.event = (*extension).first_event + f::XCB_XFIXES_CURSOR_NOTIFY as u8;
        }
        Some(connection)
    }
    fn poll(&self, pending: &mut Option<Pending>) -> Result<Option<(Allocation, u64)>, ()> {
        let Some(request) = pending.as_ref() else {
            return Ok(None);
        };
        if request.sent.elapsed() > Duration::from_millis(100) {
            return Err(());
        };
        let (mut reply, mut error) = (ptr::null_mut(), ptr::null_mut());
        // SAFETY: outputs are writable and this connection owns the live request.
        let ready = unsafe {
            (self.api.poll_reply)(
                self.raw.as_ptr(),
                request.sequence,
                &raw mut reply,
                &raw mut error,
            )
        };
        let error = NonNull::new(error.cast::<c_void>()).map(Allocation);
        let reply = NonNull::new(reply).map(Allocation);
        if error.is_some() {
            return Err(());
        };
        if ready == 0 {
            return Ok(None);
        };
        let generation = pending.take().ok_or(())?.generation;
        Ok(Some((reply.ok_or(())?, generation)))
    }
    fn cancel(&self, pending: &mut Option<Pending>) {
        if let Some(request) = pending.take() {
            // SAFETY: discard removes only this connection's outstanding request.
            unsafe { (self.api.discard)(self.raw.as_ptr(), request.sequence) };
        }
    }
    fn flush(&self) -> Result<(), ()> {
        // SAFETY: this same-thread owner retains the XCB connection.
        if unsafe {
            (self.api.error)(self.raw.as_ptr()) == 0 && (self.api.flush)(self.raw.as_ptr()) > 0
        } {
            Ok(())
        } else {
            Err(())
        }
    }
}
#[derive(Default)]
struct Metadata {
    words: Vec<u32>,
    geometry: Option<(u32, u32)>,
    position: Option<(i32, i32)>,
    valid: bool,
}
impl Metadata {
    fn image(
        &mut self,
        header: &f::xcb_xfixes_get_cursor_image_reply_t,
        pixels: &[u32],
    ) -> Result<(), ()> {
        let (width, height) = (u32::from(header.width), u32::from(header.height));
        if width == 0
            || height == 0
            || width > 256
            || height > 256
            || pixels.len() != width as usize * height as usize
            || u32::from(header.xhot) >= width
            || u32::from(header.yhot) >= height
        {
            return Err(());
        };
        self.words.resize(12 + pixels.len(), 0);
        self.words[..12].copy_from_slice(&[
            header.cursor_serial.max(1),
            0,
            0,
            0,
            u32::from(header.xhot),
            u32::from(header.yhot),
            28,
            12,
            width,
            height,
            width * 4,
            20,
        ]);
        self.words[12..].copy_from_slice(pixels);
        self.valid = true;
        Ok(())
    }
    fn get(&mut self, width: u32, height: u32) -> *const c_void {
        let Some((x, y)) = self.position else {
            return ptr::null();
        };
        if !self.valid
            || self.geometry != Some((width, height))
            || x < 0
            || y < 0
            || x as u32 >= width
            || y as u32 >= height
        {
            return ptr::null();
        };
        self.words[2] = x as u32;
        self.words[3] = y as u32;
        self.words.as_ptr().cast()
    }
}
#[derive(Default)]
struct Client {
    connection: Option<Connection>,
    attempted: bool,
    window: u32,
    version: Option<Pending>,
    ready: bool,
    pointer: Option<Pending>,
    geometry: Option<Pending>,
    image: Option<Pending>,
    generation: u64,
    dirty: bool,
    requested: Option<Instant>,
    metadata: Metadata,
    requests: [u64; 4],
    hidden: bool,
}
impl Client {
    fn step(&mut self, window: u32, width: u32, height: u32) -> Result<*const c_void, ()> {
        if window == 0 || width == 0 || height == 0 {
            return Ok(ptr::null());
        };
        if !self.attempted {
            self.attempted = true;
            self.connection = Some(Connection::open().ok_or(())?);
            let connection = self.connection.as_ref().ok_or(())?;
            // SAFETY: requests borrow the live connection; no pointers escape.
            unsafe {
                self.version = Some(Pending::new(
                    (connection.api.version)(connection.raw.as_ptr(), 4, 0).sequence,
                    0,
                )?);
                (connection.api.select)(connection.raw.as_ptr(), connection.root, 1);
            }
            connection.flush()?;
            self.dirty = true;
        }
        let Some(connection) = self.connection.as_ref() else {
            return Err(());
        };
        if let Some((reply, _)) = connection.poll(&mut self.version)? {
            // SAFETY: this is the fixed 32-byte XFixes version response.
            if unsafe { reply.read::<f::xcb_xfixes_query_version_reply_t>() }.major_version < 2 {
                return Err(());
            };
            self.ready = true;
        }
        if !self.ready {
            return Ok(ptr::null());
        };
        if self.window != window {
            // SAFETY: attribute requests copy the one event-mask scalar now.
            unsafe {
                let clear = 0u32;
                if self.window != 0 {
                    (connection.api.attributes)(
                        connection.raw.as_ptr(),
                        self.window,
                        1 << 11,
                        (&raw const clear).cast(),
                    );
                }
                let mask = 1u32 << 17;
                (connection.api.attributes)(
                    connection.raw.as_ptr(),
                    window,
                    1 << 11,
                    (&raw const mask).cast(),
                );
            }
            connection.cancel(&mut self.geometry);
            connection.cancel(&mut self.pointer);
            self.window = window;
            self.hidden = false;
            self.metadata.geometry = None;
            self.metadata.position = None;
            self.requested = None;
            // SAFETY: the geometry request borrows only the live window scalar.
            self.geometry = Some(Pending::new(
                unsafe { (connection.api.geometry)(connection.raw.as_ptr(), window) }.sequence,
                0,
            )?);
            self.requests[1] += 1;
        }
        // Bounded dispatch; hide metadata while a burst could contain a newer
        // shape/geometry event. No event subscription follows mouse motion.
        let mut drained = false;
        for _ in 0..32 {
            // SAFETY: nonblocking XCB polling transfers an owned event allocation.
            let Some(event) =
                NonNull::new(unsafe { (connection.api.event)(connection.raw.as_ptr()) })
            else {
                drained = true;
                break;
            };
            let event = Allocation(event.cast());
            // SAFETY: every XCB event has at least its fixed 32-byte protocol body.
            let kind = unsafe { *event.0.as_ptr().cast::<u8>() } & 0x7f;
            if kind == connection.event {
                self.requests[3] += 1;
                self.generation = self.generation.checked_add(1).ok_or(())?;
                self.metadata.valid = false;
                self.dirty = true;
            } else if kind == f::XCB_CONFIGURE_NOTIFY as u8 {
                // SAFETY: the event type identifies the complete fixed ConfigureNotify.
                let event = unsafe { event.read::<f::xcb_configure_notify_event_t>() };
                if event.window == window {
                    connection.cancel(&mut self.geometry);
                    connection.cancel(&mut self.pointer);
                    self.metadata.geometry =
                        Some((u32::from(event.width), u32::from(event.height)));
                    self.metadata.position = None;
                    self.requested = None;
                }
            } else if kind == f::XCB_UNMAP_NOTIFY as u8 {
                // SAFETY: the event type identifies the fixed UnmapNotify.
                if unsafe { event.read::<f::xcb_unmap_notify_event_t>() }.window == window {
                    self.hidden = true;
                    self.metadata.position = None;
                    connection.cancel(&mut self.pointer);
                }
            } else if kind == f::XCB_MAP_NOTIFY as u8 {
                // SAFETY: the event type identifies the fixed MapNotify.
                if unsafe { event.read::<f::xcb_map_notify_event_t>() }.window == window {
                    self.hidden = false;
                    self.metadata.position = None;
                    self.requested = None;
                }
            } else if kind == f::XCB_DESTROY_NOTIFY as u8 {
                // SAFETY: the event type identifies the complete fixed DestroyNotify.
                if unsafe { event.read::<f::xcb_destroy_notify_event_t>() }.window == window {
                    return Err(());
                };
            } else if kind == 0 {
                return Err(());
            };
        }
        if let Some((reply, _)) = connection.poll(&mut self.geometry)? {
            // SAFETY: this request's fixed geometry response is complete.
            let geometry = unsafe { reply.read::<f::xcb_get_geometry_reply_t>() };
            self.metadata.geometry = Some((u32::from(geometry.width), u32::from(geometry.height)));
        }
        if let Some((reply, _)) = connection.poll(&mut self.pointer)? {
            // SAFETY: this request's fixed pointer response is complete.
            let pointer = unsafe { reply.read::<f::xcb_query_pointer_reply_t>() };
            self.metadata.position = (pointer.same_screen != 0)
                .then_some((i32::from(pointer.win_x), i32::from(pointer.win_y)));
        }
        if let Some((reply, generation)) = connection.poll(&mut self.image)? {
            if generation == self.generation {
                // SAFETY: the request identifies the complete fixed image header.
                let header = unsafe { reply.read::<f::xcb_xfixes_get_cursor_image_reply_t>() };
                let count = usize::from(header.width) * usize::from(header.height);
                if header.width == 0
                    || header.height == 0
                    || header.width > 256
                    || header.height > 256
                    || header.length < count as u32
                {
                    return Err(());
                };
                // SAFETY: libxcb allocates the full advertised reply body after
                // its 32-byte header. Bounds above limit the owned pixel copy.
                let pixels = unsafe {
                    std::slice::from_raw_parts(reply.0.as_ptr().byte_add(32).cast::<u32>(), count)
                };
                self.metadata.image(&header, pixels)?;
            } else {
                self.dirty = true;
            }
        }
        if self.image.is_none() && self.dirty {
            // SAFETY: this bounded image request borrows the live connection.
            self.image = Some(Pending::new(
                unsafe { (connection.api.image)(connection.raw.as_ptr()) }.sequence,
                self.generation,
            )?);
            self.dirty = false;
            self.requests[2] += 1;
        }
        if self.pointer.is_none()
            && !self.hidden
            && self
                .requested
                .is_none_or(|time| time.elapsed() >= Duration::from_millis(16))
        {
            // SAFETY: this position request contains only the selected window ID.
            self.pointer = Some(Pending::new(
                unsafe { (connection.api.pointer)(connection.raw.as_ptr(), window) }.sequence,
                0,
            )?);
            self.requested = Some(Instant::now());
            self.requests[0] += 1;
        }
        connection.flush()?;
        Ok(if drained && !self.hidden {
            self.metadata.get(width, height)
        } else {
            ptr::null()
        })
    }
    fn update(&mut self, window: u32, width: u32, height: u32) -> *const c_void {
        match self.step(window, width, height) {
            Ok(metadata) => metadata,
            Err(()) => {
                self.connection = None;
                self.metadata.valid = false;
                ptr::null()
            }
        }
    }
}
thread_local! {static CLIENTS:Handles<Client>=Handles::new(16);}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_x11_cursor_create() -> *mut c_void {
    CLIENTS.with(|clients| {
        clients
            .insert(Client::default())
            .ok()
            .map_or(ptr::null_mut(), |token| token.get() as *mut c_void)
    })
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_x11_cursor_metadata(
    handle: *mut c_void,
    window: u32,
    width: u32,
    height: u32,
) -> *const c_void {
    with_metadata(handle as usize, window, width, height, |words| {
        words.as_ptr().cast()
    })
    .unwrap_or(ptr::null())
}
/// Consume aligned SPA cursor words while their owner is pinned and locked.
/// The callback must not reenter this thread's cursor table. Rust borrows cannot
/// escape the callback, even if a caller knows or guesses a retired handle.
pub fn with_metadata<R>(
    handle: usize,
    window: u32,
    width: u32,
    height: u32,
    consume: impl FnOnce(&[u32]) -> R,
) -> Option<R> {
    CLIENTS.with(|clients| {
        clients
            .with_mut(handle, |client| {
                if client.update(window, width, height).is_null() {
                    None
                } else {
                    Some(consume(&client.metadata.words))
                }
            })
            .flatten()
    })
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_x11_cursor_destroy(handle: *mut c_void) {
    CLIENTS.with(|clients| drop(clients.remove(handle as usize)));
}
#[unsafe(no_mangle)]
/// # Safety
/// `output` must point to four writable `u64` counters when nonnull.
pub unsafe extern "C" fn dpe_x11_cursor_stats(handle: *mut c_void, output: *mut u64) {
    if output.is_null() {
        return;
    };
    let values = CLIENTS.with(|clients| {
        clients
            .with_mut(handle as usize, |client| client.requests)
            .unwrap_or([0; 4])
    });
    // SAFETY: the caller supplies four writable u64 counters.
    unsafe { ptr::copy_nonoverlapping(values.as_ptr(), output, 4) };
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cursor_pixels_are_owned_bounded_and_geometry_changes_hide_stale_metadata() {
        let mut metadata = Metadata::default();
        assert!(metadata.words.is_empty());
        let mut header = f::xcb_xfixes_get_cursor_image_reply_t {
            width: 2,
            height: 2,
            xhot: 1,
            yhot: 1,
            cursor_serial: 7,
            ..Default::default()
        };
        metadata.image(&header, &[1, 2, 3, 4]).unwrap();
        assert_eq!(metadata.words.len(), 16);
        metadata.geometry = Some((16, 16));
        metadata.position = Some((5, 6));
        assert!(!metadata.get(16, 16).is_null());
        assert_eq!(
            metadata.words[..12],
            [7, 0, 5, 6, 1, 1, 28, 12, 2, 2, 8, 20]
        );
        assert_eq!(&metadata.words[12..], &[1, 2, 3, 4]);
        assert!(metadata.get(32, 16).is_null());
        metadata.position = Some((-1, 6));
        assert!(metadata.get(16, 16).is_null());
        metadata.position = Some((16, 6));
        assert!(metadata.get(16, 16).is_null());
        header.width = 257;
        assert!(metadata.image(&header, &[1, 2, 3, 4]).is_err());
        header.width = 2;
        header.xhot = 2;
        assert!(metadata.image(&header, &[1, 2, 3, 4]).is_err());
        header.xhot = 1;
        assert!(metadata.image(&header, &[1, 2, 3]).is_err());
    }
    #[test]
    fn opaque_handles_are_thread_confined_bounded_and_never_reused() {
        let handles = (0..16).map(|_| dpe_x11_cursor_create()).collect::<Vec<_>>();
        assert!(handles.iter().all(|handle| !handle.is_null()));
        assert!(dpe_x11_cursor_create().is_null());
        let stale = handles[0] as usize;
        std::thread::spawn(move || {
            assert!(dpe_x11_cursor_metadata(stale as *mut c_void, 1, 16, 16).is_null());
            dpe_x11_cursor_destroy(stale as *mut c_void);
        })
        .join()
        .unwrap();
        dpe_x11_cursor_destroy(handles[0]);
        assert!(dpe_x11_cursor_metadata(handles[0], 1, 16, 16).is_null());
        let fresh = dpe_x11_cursor_create();
        assert!(!fresh.is_null() && fresh != handles[0]);
        for handle in handles {
            dpe_x11_cursor_destroy(handle);
        }
        dpe_x11_cursor_destroy(fresh);
    }
}
