// SPDX-License-Identifier: MIT
//! Native rendering compatibility, kept out of the encoder and capture threads.
use crate::{ffi, gpu::Thumbnail};
use dpe_core::{Latest, Next};
use std::{
    ffi::{CStr, CString, c_void},
    os::raw::c_char,
    ptr,
    ptr::NonNull,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicU64, Ordering},
    },
    thread::JoinHandle,
    time::Instant,
};

unsafe extern "C" {
    fn dpe_direct_frame_owned(frame: *const c_void) -> bool;
    fn dpe_direct_frame_view(
        frame: *mut c_void,
        view: *mut ffi::encoder_nv12,
        lease: *mut *mut c_void,
        node: *mut *const c_char,
    ) -> bool;
    fn dpe_direct_frame_release(lease: *mut c_void);
}
struct Source {
    key: usize,
    view: ffi::encoder_nv12,
    lease: NonNull<c_void>,
    node: CString,
}
// SAFETY: the C capture API explicitly permits reference release on any thread;
// this lease prevents output reuse until the independently synchronized preview finishes.
unsafe impl Send for Source {}
impl Drop for Source {
    fn drop(&mut self) {
        // SAFETY: exactly one Source owns the lease returned by frame_view.
        unsafe {
            dpe_direct_frame_release(self.lease.as_ptr());
        }
    }
}
impl Source {
    unsafe fn acquire(frame: *mut c_void) -> Option<Self> {
        let mut view = ffi::encoder_nv12::default();
        let mut lease = ptr::null_mut();
        let mut node = ptr::null();
        // SAFETY: caller holds the native input frame for this callback. All output storage is live.
        if !unsafe { dpe_direct_frame_view(frame, &mut view, &mut lease, &mut node) } {
            return None;
        }
        let lease = NonNull::new(lease)?;
        if node.is_null() {
            // SAFETY: frame_view transferred one reference even on this invalid-node defensive path.
            unsafe {
                dpe_direct_frame_release(lease.as_ptr());
            }
            return None;
        }
        // SAFETY: frame_view's node string remains valid under its returned lease.
        let key = node as usize;
        // SAFETY: the node string remains valid for the returned allocation lease.
        let node = unsafe { CStr::from_ptr(node) }.to_owned();
        Some(Self {
            key,
            view,
            lease,
            node,
        })
    }
}

type Original = unsafe extern "C" fn(*mut c_void, *const c_void);
type Handle = unsafe extern "C" fn(*const c_void) -> *mut c_void;
type Deliver = unsafe extern "C" fn(
    *const c_char,
    *const ffi::dpe_discord_frame,
    unsafe extern "C" fn(*mut c_void),
    *mut c_void,
);
pub struct Hook {
    pub original: Original,
    pub handle: Handle,
    pub buffer_vtable: usize,
}
static HOOK: OnceLock<Hook> = OnceLock::new();
static WORKER: OnceLock<Worker> = OnceLock::new();
static REQUESTS: AtomicU64 = AtomicU64::new(0);
static FRAMES: AtomicU64 = AtomicU64::new(0);
static BYTES: AtomicU64 = AtomicU64::new(0);
static DROPPED: AtomicU64 = AtomicU64::new(0);
static ERRORS: AtomicU64 = AtomicU64::new(0);
#[derive(Clone)]
struct Destination {
    key: usize,
    id: CString,
    deliver: Deliver,
    timestamp: i64,
    seen: Instant,
}
static DESTINATIONS: OnceLock<Mutex<std::collections::HashMap<CString, Destination>>> =
    OnceLock::new();
struct Request {
    source: Source,
}
struct Pixels {
    data: Vec<u8>,
    width: u32,
    height: u32,
}
struct Worker {
    queue: Arc<Latest<Request>>,
    thread: Mutex<Option<JoinHandle<()>>>,
}
impl Worker {
    fn new() -> Self {
        let queue = Arc::new(Latest::default());
        let pending = queue.clone();
        let thread = std::thread::Builder::new()
            .name("DPE preview".into())
            .spawn(move || run(pending))
            .ok();
        if thread.is_none() {
            drop(queue.close());
        }
        Self {
            queue,
            thread: Mutex::new(thread),
        }
    }
}
fn run(queue: Arc<Latest<Request>>) {
    // Preview is optional background GPU/CPU work. Capture, encoder, game and
    // compositor priorities remain unchanged; scheduling failure is nonfatal.
    let _ = dpe_os::scheduling::background_current_thread();
    let mut gpu: Option<(CString, Thumbnail)> = None;
    let mut pool = [
        Arc::new(Pixels {
            data: Vec::new(),
            width: 0,
            height: 0,
        }),
        Arc::new(Pixels {
            data: Vec::new(),
            width: 0,
            height: 0,
        }),
    ];
    loop {
        let request = match queue.receive_timeout(std::time::Duration::from_millis(500)) {
            Next::Item(request) => request,
            Next::Closed => break,
            Next::TimedOut => {
                gpu = None;
                if let Some(map) = DESTINATIONS.get() {
                    map.lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .clear();
                }
                continue;
            }
        };
        // DirectOutputStream also feeds the enlarged local share. Preserve the
        // capture dimensions here; picker thumbnails have a separate path.
        let (w, h) = (request.source.view.width, request.source.view.height);
        if gpu
            .as_ref()
            .is_none_or(|(node, g)| node != &request.source.node || g.size() != (w, h))
        {
            gpu = None;
            match Thumbnail::new(&request.source.node, w, h) {
                Ok(g) => gpu = Some((request.source.node.clone(), g)),
                Err(_) => {
                    ERRORS.fetch_add(1, Ordering::Relaxed);
                    continue;
                }
            }
        }
        let Some(index) = pool.iter().position(|p| Arc::strong_count(p) == 1) else {
            DROPPED.fetch_add(1, Ordering::Relaxed);
            continue;
        };
        let Some(pixel) = Arc::get_mut(&mut pool[index]) else {
            continue;
        };
        let Some((_, converter)) = gpu.as_mut() else {
            continue;
        };
        if converter
            .convert(&request.source.view, &mut pixel.data)
            .is_err()
        {
            ERRORS.fetch_add(1, Ordering::Relaxed);
            if converter.pending() {
                // A GPU failure cannot authorize reusing a source still being read.
                // Quarantine this single lease until process exit and stop this worker.
                // This bounded failure leak is preferable to concurrent GPU writes.
                std::mem::forget(request.source);
                drop(queue.close());
                return;
            }
            gpu = None;
            continue;
        }
        pixel.width = w;
        pixel.height = h;
        let pixels = pool[index].clone();
        let bytes = pixels.data.len();
        let p = &pixels.data;
        let y = (w * h) as usize;
        let frame = ffi::dpe_discord_frame {
            timestamp_us: 0,
            frame: ffi::dpe_discord_frame__bindgen_ty_1 {
                yuv: ffi::dpe_discord_yuv {
                    y: p.as_ptr(),
                    u: p[y..].as_ptr(),
                    v: p[y * 5 / 4..].as_ptr(),
                    y_stride: w as i32,
                    u_stride: (w / 2) as i32,
                    v_stride: (w / 2) as i32,
                },
            },
            width: w as i32,
            height: h as i32,
            type_: 1,
            rotation: 0,
        };
        let destinations = DESTINATIONS
            .get()
            .map(|map| {
                map.lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .values()
                    .filter(|d| d.key == request.source.key && d.seen.elapsed().as_millis() < 500)
                    .cloned()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        for destination in destinations {
            let _profile = dpe_os::profiler::Span::new(25);
            let mut frame = frame;
            frame.timestamp_us = destination.timestamp;
            let owned = Arc::into_raw(pixels.clone()).cast_mut().cast();
            // SAFETY: SDK copies the descriptor synchronously. Each completion owns
            // one Arc, including missing-stream paths; all planes remain immutable.
            unsafe {
                (destination.deliver)(destination.id.as_ptr(), &frame, release_pixels, owned);
            }
        }
        FRAMES.fetch_add(1, Ordering::Relaxed);
        BYTES.fetch_add(bytes as u64, Ordering::Relaxed);
    }
}
unsafe extern "C" fn release_pixels(pointer: *mut c_void) {
    // SAFETY: one Arc reference was transferred to exactly this SDK completion callback.
    unsafe {
        drop(Arc::from_raw(pointer.cast::<Pixels>()));
    }
}
pub fn install(hook: Hook) -> bool {
    HOOK.set(hook).is_ok()
}
pub fn shutdown() {
    if let Some(worker) = WORKER.get() {
        drop(worker.queue.close());
        if let Some(t) = worker
            .thread
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take()
        {
            let _ = t.join();
        }
    }
}

/// Called only by the exact reviewed DirectOutputStream vtable slot.
/// Foreign buffers always retain Discord's original rendering implementation.
pub unsafe extern "C" fn on_frame(stream: *mut c_void, frame: *const c_void) {
    let Some(hook) = HOOK.get() else {
        return;
    };
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if stream.is_null() || frame.is_null() {
            return;
        }
        // SAFETY: reviewed VideoFrame::video_frame_buffer() proves the offset;
        // the call owns VideoFrame and its refcounted buffer for this callback.
        let buffer = unsafe { frame.byte_add(8).cast::<*const c_void>().read() };
        // SAFETY: every non-null native VideoFrame buffer begins with its C++ vptr.
        let owned_type =
            !buffer.is_null() && unsafe { buffer.cast::<usize>().read() } == hook.buffer_vtable;
        if !owned_type {
            // SAFETY: preserve original callback arguments for all foreign buffer types.
            unsafe {
                (hook.original)(stream, frame);
            }
            return;
        }
        // SAFETY: exact WumpusFrameBuffer vptr and reviewed accessor bytes establish this call.
        let handle = unsafe { (hook.handle)(buffer) };
        // SAFETY: membership lookup compares identities; it does not dereference foreign handles.
        if !unsafe { dpe_direct_frame_owned(handle) } {
            if dpe_core::handles::pointer_reserved(handle as usize) {
                return;
            }
            // SAFETY: this is a foreign native frame, passed unchanged to its original callback.
            unsafe {
                (hook.original)(stream, frame);
            }
            return;
        }
        // SAFETY: reviewed DirectOutputStream holds an immutable libc++ stream id
        // and the public SDK DeliverDiscordFrame callback for this OnFrame lifetime.
        let callback = unsafe { stream.byte_add(0x30).cast::<usize>().read() };
        if callback == 0 {
            return;
        }
        // SAFETY: exact reviewed libc++ string representation; inspect its short/long tag.
        let is_long = unsafe { stream.byte_add(0x2f).cast::<u8>().read() } & 0x80 != 0;
        // SAFETY: immutable string storage is owned by the live DirectOutputStream.
        let id = if is_long {
            // SAFETY: reviewed long-string representation stores its live pointer here.
            unsafe { stream.byte_add(0x18).cast::<*const c_char>().read() }
        } else {
            // SAFETY: reviewed short-string representation stores its bytes inline.
            unsafe { stream.byte_add(0x18) }.cast()
        };
        if id.is_null() {
            return;
        }
        // SAFETY: native string is NUL-terminated and is copied before the callback returns.
        let id = unsafe { CStr::from_ptr(id) }.to_owned();
        if id.as_bytes().len() > 256 {
            return;
        }
        // SAFETY: native reviewed VideoFrame timestamp is an int64 at offset 0x20.
        let timestamp = unsafe { frame.byte_add(0x20).cast::<i64>().read() };
        // SAFETY: native callback field has the public SDK Deliver signature, checked at install.
        let deliver = unsafe { std::mem::transmute::<usize, Deliver>(callback) };
        // SAFETY: input frame is still held by the OnFrame caller.
        let Some(source) = (unsafe { Source::acquire(handle) }) else {
            return;
        };
        {
            let mut map = DESTINATIONS
                .get_or_init(Default::default)
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            map.retain(|_, d| d.seen.elapsed().as_secs() < 1);
            if map.len() >= 16 && !map.contains_key(&id) {
                return;
            }
            map.insert(
                id.clone(),
                Destination {
                    key: source.key,
                    id,
                    deliver,
                    timestamp,
                    seen: Instant::now(),
                },
            );
        }
        // Follow capture cadence. The latest-only queue and leased pixel pool
        // bound pending work without imposing a thumbnail frame-rate cap.
        REQUESTS.fetch_add(1, Ordering::Relaxed);
        match WORKER
            .get_or_init(Worker::new)
            .queue
            .replace(Request { source })
        {
            Ok(Some(old)) => {
                DROPPED.fetch_add(1, Ordering::Relaxed);
                drop(old);
            }
            Err(value) => {
                DROPPED.fetch_add(1, Ordering::Relaxed);
                drop(value);
            }
            Ok(None) => {}
        }
    }));
    if result.is_err() {
        ERRORS.fetch_add(1, Ordering::Relaxed);
    }
}
#[unsafe(no_mangle)]
/// # Safety
/// The caller supplies five writable uint64_t entries.
pub unsafe extern "C" fn dpe_rust_preview_stats(output: *mut u64) {
    if output.is_null() {
        return;
    }
    // SAFETY: the C caller provides five writable uint64_t entries.
    unsafe {
        ptr::copy_nonoverlapping(
            [
                REQUESTS.load(Ordering::Relaxed),
                FRAMES.load(Ordering::Relaxed),
                BYTES.load(Ordering::Relaxed),
                DROPPED.load(Ordering::Relaxed),
                ERRORS.load(Ordering::Relaxed),
            ]
            .as_ptr(),
            output,
            5,
        );
    }
}
