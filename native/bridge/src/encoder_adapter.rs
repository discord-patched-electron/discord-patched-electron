// SPDX-License-Identifier: MIT
//! Native encoder interception. The private backend owns codec serialization;
//! this layer owns admission, foreign leases and the reviewed loader boundary.
use crate::{
    call_patch::{Module, Slot, Slots},
    ffi as f,
    node::Library,
};
use dpe_core::{
    admission::{Admission, lock},
    handles::{Handles, PointerKind, pointer_reserved},
};
use std::{
    ffi::{CStr, CString, c_char, c_void},
    fs::File,
    io::Write,
    os::fd::{AsRawFd, FromRawFd},
    ptr,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering},
    },
};
type Native =
    unsafe extern "C" fn(*mut c_void, *const c_void, *mut c_void, f::encoder_frame_info) -> bool;
type Cpu = unsafe extern "C" fn(
    *mut c_void,
    *const c_void,
    f::encoder_frame_info,
    f::encoder_plane,
    f::encoder_plane,
    f::encoder_plane,
) -> bool;
type Vendor = unsafe extern "C" fn(*mut c_void) -> u32;
type Recon = unsafe extern "C" fn(*mut c_void) -> bool;
type Probe = unsafe extern "C" fn(*const c_char, *mut c_void);
type Describe = unsafe extern "C" fn(*const c_void) -> *const u64;
type DropLinux = unsafe extern "C" fn(*mut *mut c_void);
type CloneCpu = unsafe extern "C" fn(
    *mut c_void,
    *mut *const u8,
    *mut usize,
    *mut usize,
    *mut usize,
    *mut usize,
    *mut usize,
) -> *mut c_void;
type DropCpu = unsafe extern "C" fn(*mut c_void);
struct Hooks {
    module: Module,
    native: Native,
    cpu: Cpu,
    vendor: Vendor,
    recon: Recon,
    probe: Probe,
    describe: Describe,
    drop_linux: DropLinux,
    clone_cpu: CloneCpu,
    drop_cpu: DropCpu,
}
struct Backend {
    api: f::dpe_hevc_api,
    imports: Option<unsafe extern "C" fn(*mut u64)>,
    scope: Option<Vendor>,
    profile: Option<unsafe extern "C" fn(u32, *mut u64) -> bool>,
    _library: Library,
}
static HOOKS: OnceLock<Hooks> = OnceLock::new();
static BACKEND: OnceLock<Backend> = OnceLock::new();
static INSTALL: Mutex<()> = Mutex::new(());
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
static PROBES: AtomicU64 = AtomicU64::new(0);
static DIRECT: AtomicU64 = AtomicU64::new(0);
static AV1_SCOPES: AtomicU32 = AtomicU32::new(0);
#[derive(Default)]
struct Counts {
    gpu: AtomicU64,
    cpu: AtomicU64,
    fallback: AtomicU64,
    failed: AtomicU64,
}
fn counts(codec: u32) -> &'static Counts {
    static COUNTS: OnceLock<[Counts; 3]> = OnceLock::new();
    &COUNTS.get_or_init(Default::default)[codec as usize]
}
struct Devices {
    vendors: [Option<CString>; 2],
    av1: Option<CString>,
}
static DEVICES: OnceLock<Devices> = OnceLock::new();
fn devices() -> &'static Devices {
    DEVICES.get_or_init(|| {
        let mut result = Devices {
            vendors: [None, None],
            av1: None,
        };
        let Some(backend) = BACKEND.get() else {
            return result;
        };
        let Ok(entries) = std::fs::read_dir("/dev/dri") else {
            return result;
        };
        let mut nodes = entries
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| {
                        name.strip_prefix("renderD").is_some_and(|number| {
                            !number.is_empty() && number.bytes().all(|byte| byte.is_ascii_digit())
                        })
                    })
            })
            .collect::<Vec<_>>();
        nodes.sort();
        for node in nodes.into_iter().take(128) {
            let Some(name) = node.file_name() else {
                continue;
            };
            let Ok(vendor) = std::fs::read_to_string(
                std::path::Path::new("/sys/class/drm")
                    .join(name)
                    .join("device/vendor"),
            ) else {
                continue;
            };
            let index = match vendor.trim() {
                "0x1002" => 0,
                "0x8086" => 1,
                _ => continue,
            };
            use std::os::unix::ffi::OsStrExt;
            let Ok(node) = CString::new(node.as_os_str().as_bytes()) else {
                continue;
            };
            if index == 0 && result.av1.is_none() {
                result.av1 = Some(node.clone());
            }
            if result.vendors[index].is_some() {
                continue;
            };
            PROBES.fetch_add(1, Ordering::Relaxed);
            // SAFETY: the validated private API borrows this terminated render-node path.
            if unsafe { backend.api.probe.unwrap()(node.as_ptr()) } == index as u32 + 1 {
                result.vendors[index] = Some(node);
            }
        }
        result
    })
}
struct Encoder {
    backend: &'static Backend,
    handle: usize,
    codec: u32,
    vendor: u32,
    admission: Arc<Admission>,
    destroyed: AtomicBool,
}
impl Encoder {
    fn destroy(&self) {
        self.admission.close();
        if !self.destroyed.swap(true, Ordering::AcqRel) {
            // SAFETY: the private ABI 2 backend closes admission and waits for
            // foreign callbacks, or cancels later notifications on reentrancy.
            unsafe { self.backend.api.destroy.unwrap()(self.handle as *mut c_void) };
        }
    }
}
impl Drop for Encoder {
    fn drop(&mut self) {
        self.destroy();
    }
}
fn encoders() -> &'static Handles<Arc<Encoder>> {
    static TABLE: OnceLock<Handles<Arc<Encoder>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::pointers(128, PointerKind::EncoderAdapter))
}
static ACTIVE: AtomicUsize = AtomicUsize::new(0);
unsafe fn create_at(
    node: &CStr,
    context: *mut c_void,
    encoded: *mut c_void,
    dropped: *mut c_void,
    output: *mut *mut c_void,
    session: f::encoder_session,
    options: f::encoder_options,
) -> i32 {
    let Some(backend) = BACKEND.get() else {
        return 0;
    };
    // SAFETY: the reviewed caller supplies callbacks with the declared by-value
    // ABI and keeps their context alive until destroy returns.
    let handle = unsafe {
        backend.api.create.unwrap()(
            node.as_ptr(),
            session,
            options,
            context,
            Some(std::mem::transmute::<
                *mut c_void,
                unsafe extern "C" fn(
                    *mut c_void,
                    *const f::encoder_frame_meta,
                    u64,
                    *const u8,
                    usize,
                    *const c_void,
                ),
            >(encoded)),
            Some(std::mem::transmute::<
                *mut c_void,
                unsafe extern "C" fn(*mut c_void, *const f::encoder_frame_meta),
            >(dropped)),
        )
    };
    if handle.is_null() {
        return 0;
    };
    // SAFETY: this live private encoder was just created by this API.
    let vendor = unsafe { backend.api.vendor.unwrap()(handle) };
    let entry = Arc::new(Encoder {
        backend,
        handle: handle as usize,
        codec: session.codec,
        vendor,
        admission: Arc::default(),
        destroyed: AtomicBool::new(false),
    });
    let Ok(token) = encoders().insert(entry.clone()) else {
        return 0;
    };
    if session.codec == 2 {
        // SAFETY: the optional accessor borrows this live encoder only.
        let known = backend
            .scope
            .is_some_and(|scope| unsafe { scope(handle) == 1 });
        AV1_SCOPES.fetch_or(if known { 1 } else { 2 }, Ordering::Relaxed);
    }
    ACTIVE.fetch_add(1, Ordering::Relaxed);
    // SAFETY: caller supplies a writable output pointer; token is opaque only.
    unsafe { *output = token.get() as *mut c_void };
    1
}
unsafe fn create(
    selection: (u32, u32),
    context: *mut c_void,
    encoded: *mut c_void,
    dropped: *mut c_void,
    output: *mut *mut c_void,
    session: f::encoder_session,
    options: f::encoder_options,
) -> i32 {
    let (codec, kind) = selection;
    if INSTALLED.load(Ordering::Acquire) == 0 || session.codec != codec || !matches!(kind, 5 | 6) {
        return -1;
    };
    if output.is_null() || encoded.is_null() || dropped.is_null() {
        return 0;
    };
    // SAFETY: output belongs to the reviewed factory call.
    unsafe { *output = ptr::null_mut() };
    let devices = devices();
    let nodes: Vec<&CStr> = if codec == 2 {
        devices.av1.iter().map(|node| node.as_c_str()).collect()
    } else {
        devices
            .vendors
            .iter()
            .flatten()
            .map(|node| node.as_c_str())
            .collect()
    };
    for node in nodes {
        // SAFETY: callbacks and output are checked above, node is a live CString.
        if unsafe { create_at(node, context, encoded, dropped, output, session, options) } == 1 {
            return 1;
        };
    }
    0
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_hevc_create(
    kind: u32,
    context: *mut c_void,
    encoded: *mut c_void,
    dropped: *mut c_void,
    output: *mut *mut c_void,
    session: f::encoder_session,
    options: f::encoder_options,
) -> i32 {
    // SAFETY: forwards the reviewed C factory contract without changing ownership.
    unsafe {
        create(
            (1, kind),
            context,
            encoded,
            dropped,
            output,
            session,
            options,
        )
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_av1_create(
    kind: u32,
    context: *mut c_void,
    encoded: *mut c_void,
    dropped: *mut c_void,
    output: *mut *mut c_void,
    session: f::encoder_session,
    options: f::encoder_options,
) -> i32 {
    // SAFETY: forwards the reviewed C factory contract without changing ownership.
    unsafe {
        create(
            (2, kind),
            context,
            encoded,
            dropped,
            output,
            session,
            options,
        )
    }
}
struct DirectLease(*mut c_void);
impl Drop for DirectLease {
    fn drop(&mut self) {
        // SAFETY: a successful direct-frame view transfers exactly one release ticket.
        unsafe { f::dpe_direct_frame_release(self.0) };
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_encoder_create(
    kind: u32,
    frame: *const c_void,
    context: *mut c_void,
    encoded: *mut c_void,
    dropped: *mut c_void,
    output: *mut *mut c_void,
    session: f::encoder_session,
    options: f::encoder_options,
) -> i32 {
    // SAFETY: ownership lookup treats the pointer only as an opaque token.
    if !unsafe { f::dpe_direct_frame_owned(frame) } {
        return -1;
    };
    if !matches!(kind, 5 | 6)
        || INSTALLED.load(Ordering::Acquire) == 0
        || session.codec > 2
        || output.is_null()
        || encoded.is_null()
        || dropped.is_null()
    {
        return 0;
    };
    let (mut view, mut lease, mut node) =
        (f::encoder_nv12::default(), ptr::null_mut(), ptr::null());
    // SAFETY: lookup validates the token; all output records are writable.
    unsafe { *output = ptr::null_mut() };
    // SAFETY: the direct-frame API validates the token and pins the returned node.
    if !unsafe {
        f::dpe_direct_frame_view(
            frame.cast_mut(),
            &raw mut view,
            &raw mut lease,
            &raw mut node,
        )
    } {
        return 0;
    };
    let _lease = DirectLease(lease);
    if node.is_null() {
        return 0;
    };
    // SAFETY: the held direct lease pins the terminated render-node string.
    unsafe {
        create_at(
            CStr::from_ptr(node),
            context,
            encoded,
            dropped,
            output,
            session,
            options,
        )
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hevc_owned(handle: *mut c_void) -> bool {
    encoders().contains(handle as usize)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hevc_active() -> bool {
    ACTIVE.load(Ordering::Relaxed) != 0
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hevc_update(handle: *mut c_void, options: f::encoder_options) -> bool {
    let Some(entry) = encoders().get(handle as usize) else {
        return false;
    };
    let Some(_operation) = entry.admission.enter() else {
        return false;
    };
    // SAFETY: admission pins the callback context; ABI 2 serializes codec changes.
    unsafe { entry.backend.api.update.unwrap()(entry.handle as *mut c_void, options) }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hevc_destroy(handle: *mut c_void) -> bool {
    let Some(entry) = encoders().remove(handle as usize) else {
        return false;
    };
    ACTIVE.fetch_sub(1, Ordering::Relaxed);
    entry.destroy();
    true
}
unsafe extern "C" fn vendor(handle: *mut c_void) -> u32 {
    if let Some(entry) = encoders().get(handle as usize) {
        return entry.vendor;
    };
    if pointer_reserved(handle as usize) {
        return 4;
    };
    // SAFETY: foreign runtime handles retain the original C ABI.
    HOOKS
        .get()
        .map_or(4, |hooks| unsafe { (hooks.vendor)(handle) })
}
unsafe extern "C" fn recon(handle: *mut c_void) -> bool {
    if pointer_reserved(handle as usize) || dpe_hevc_owned(handle) {
        return false;
    };
    // SAFETY: foreign runtime handles retain the original C ABI.
    HOOKS
        .get()
        .is_some_and(|hooks| unsafe { (hooks.recon)(handle) })
}
unsafe extern "C" fn probe(helper: *const c_char, support: *mut c_void) {
    let Some(hooks) = HOOKS.get() else { return };
    // SAFETY: the reviewed call passes the original GPU-support output structure.
    unsafe { (hooks.probe)(helper, support) };
    if support.is_null() {
        return;
    };
    for (index, offset) in [4, 7].into_iter().enumerate() {
        if devices().vendors[index].is_some() {
            // SAFETY: these reviewed byte offsets are within the support structure.
            unsafe { *support.cast::<u8>().add(offset) = 1 };
        }
    }
}
struct CpuLease(*mut c_void, DropCpu);
impl Drop for CpuLease {
    fn drop(&mut self) {
        // SAFETY: the native clone factory transferred exactly one I420 lease.
        unsafe { (self.1)(self.0) };
    }
}
unsafe extern "C" fn release_linux(opaque: *mut c_void) {
    let Some(hooks) = HOOKS.get() else { return };
    // SAFETY: each lease pins the reviewed Arc allocation's aligned strong word.
    let strong = unsafe { &*opaque.cast::<AtomicUsize>() };
    if strong.fetch_sub(1, Ordering::Release) == 1 {
        std::sync::atomic::fence(Ordering::Acquire);
        let mut opaque = opaque;
        // SAFETY: last strong owner runs the checksum-bound Rust drop_slow function.
        unsafe { (hooks.drop_linux)(&raw mut opaque) };
    }
}
unsafe fn gpu_view(hooks: &Hooks, frame: *mut c_void) -> Option<(f::encoder_nv12, *mut c_void)> {
    // SAFETY: caller excluded DPE token pointers. The retained, hash-reviewed
    // native CcFrameHandle owns these borrowed fields throughout this call.
    unsafe {
        if ptr::read_unaligned(frame.cast::<u32>()) != 1 {
            return None;
        };
        let arc = ptr::read_unaligned(frame.byte_add(8).cast::<*mut c_void>());
        if arc.is_null() {
            return None;
        };
        let pooled = ptr::read_unaligned(arc.byte_add(16).cast::<*mut c_void>());
        if pooled.is_null() || ptr::read_unaligned(pooled.byte_add(16 + 0xb0).cast::<u32>()) == 2 {
            return None;
        };
        let descriptor = (hooks.describe)(pooled.byte_add(16));
        if descriptor.is_null() {
            return None;
        };
        let descriptor = std::slice::from_raw_parts(descriptor, 13);
        if descriptor[2] != 2
            || descriptor[10] != 1
            || descriptor[12] != 1
            || descriptor[3] as u32 != u32::from_le_bytes(*b"NV12")
        {
            return None;
        };
        let planes = descriptor[1] as *const u64;
        let fd = descriptor[11] as *const i32;
        if planes.is_null() || fd.is_null() {
            return None;
        };
        let planes = std::slice::from_raw_parts(planes, 6);
        if planes[0] != 0 || planes[3] != 0 {
            return None;
        };
        let view = f::encoder_nv12 {
            width: descriptor[6].try_into().ok()?,
            height: descriptor[7].try_into().ok()?,
            fd: *fd,
            size: descriptor[8].try_into().ok()?,
            offset: [planes[1].try_into().ok()?, planes[4].try_into().ok()?],
            stride: [planes[2].try_into().ok()?, planes[5].try_into().ok()?],
            modifier: descriptor[4],
        };
        // Reject overflow without briefly publishing an invalid strong count.
        (&*arc.cast::<AtomicUsize>())
            .try_update(Ordering::Relaxed, Ordering::Relaxed, |strong| {
                (strong > 0 && strong < isize::MAX as usize).then(|| strong + 1)
            })
            .ok()?;
        Some((view, arc))
    }
}
unsafe fn fallback(
    entry: &Encoder,
    hooks: &Hooks,
    frame: *mut c_void,
    meta: *const f::encoder_frame_meta,
    info: f::encoder_frame_info,
) -> bool {
    let (mut pixels, mut size, mut sy, mut suv, mut u, mut v) = (ptr::null(), 0, 0, 0, 0, 0);
    // SAFETY: the checksum-bound native clone API returns a pinned plane buffer.
    let lease = unsafe {
        (hooks.clone_cpu)(
            frame,
            &raw mut pixels,
            &raw mut size,
            &raw mut sy,
            &raw mut suv,
            &raw mut u,
            &raw mut v,
        )
    };
    if lease.is_null() {
        return false;
    };
    let _lease = CpuLease(lease, hooks.drop_cpu);
    // SAFETY: the reviewed caller supplies a live FrameMeta, checked nonnull above.
    let (width, height) = unsafe { ((*meta).width as usize, (*meta).height as usize) };
    let (cw, ch) = (width.div_ceil(2), height.div_ceil(2));
    if pixels.is_null()
        || width == 0
        || height == 0
        || size > isize::MAX as usize
        || sy < width
        || suv < cw
        || sy > size / height
        || u > size
        || v > size
        || suv > (size - u) / ch
        || suv > (size - v) / ch
    {
        return false;
    };
    // SAFETY: the clone pins the allocation and checked byte spans cover all planes.
    let planes = unsafe {
        [
            f::encoder_plane {
                data: pixels,
                stride: sy,
                width,
                height,
            },
            f::encoder_plane {
                data: pixels.add(u),
                stride: suv,
                width: cw,
                height: ch,
            },
            f::encoder_plane {
                data: pixels.add(v),
                stride: suv,
                width: cw,
                height: ch,
            },
        ]
    };
    counts(entry.codec).cpu.fetch_add(1, Ordering::Relaxed);
    counts(entry.codec).fallback.fetch_add(1, Ordering::Relaxed);
    // SAFETY: ABI 2 borrows planes only until return, while the clone lease lives.
    unsafe {
        entry.backend.api.i420.unwrap()(entry.handle as *mut c_void, meta, info, planes.as_ptr())
    }
}
unsafe extern "C" fn native(
    handle: *mut c_void,
    meta: *const c_void,
    frame: *mut c_void,
    info: f::encoder_frame_info,
) -> bool {
    let Some(entry) = encoders().get(handle as usize) else {
        // SAFETY: this lookup treats a possibly foreign pointer only as an opaque token.
        if pointer_reserved(handle as usize)
            || pointer_reserved(frame as usize)
            // SAFETY: ownership lookup never dereferences a foreign pointer.
            || unsafe { f::dpe_direct_frame_owned(frame) }
        {
            return false;
        };
        // SAFETY: foreign frames and handles preserve the original reviewed C ABI.
        return HOOKS
            .get()
            .is_some_and(|hooks| unsafe { (hooks.native)(handle, meta, frame, info) });
    };
    let Some(_operation) = entry.admission.enter() else {
        return false;
    };
    if meta.is_null() || frame.is_null() {
        counts(entry.codec).failed.fetch_add(1, Ordering::Relaxed);
        return false;
    };
    let Some(hooks) = HOOKS.get() else {
        return false;
    };
    let (mut view, mut lease) = (f::encoder_nv12::default(), ptr::null_mut());
    // SAFETY: this API validates the frame token and writes complete view/lease outputs.
    let direct =
        unsafe { f::dpe_direct_frame_view(frame, &raw mut view, &raw mut lease, ptr::null_mut()) };
    let result = if direct {
        DIRECT.fetch_add(1, Ordering::Relaxed);
        counts(entry.codec).gpu.fetch_add(1, Ordering::Relaxed);
        // SAFETY: the backend consumes the ticket exactly once on success or failure.
        unsafe {
            entry.backend.api.nv12.unwrap()(
                entry.handle as *mut c_void,
                meta.cast(),
                info,
                &raw const view,
                lease,
                Some(f::dpe_direct_frame_release),
            )
        }
    } else if pointer_reserved(frame as usize) {
        false
    }
    // SAFETY: the borrowed foreign frame is excluded from the DPE token namespace.
    else if let Some((view, lease)) = unsafe { gpu_view(hooks, frame) } {
        counts(entry.codec).gpu.fetch_add(1, Ordering::Relaxed);
        // SAFETY: the validated Arc lease is consumed exactly once by the backend.
        unsafe {
            entry.backend.api.nv12.unwrap()(
                entry.handle as *mut c_void,
                meta.cast(),
                info,
                &raw const view,
                lease,
                Some(release_linux),
            )
        }
    } else {
        // SAFETY: this is a live foreign frame from the reviewed native entry point.
        unsafe { fallback(&entry, hooks, frame, meta.cast(), info) }
    };
    if !result {
        counts(entry.codec).failed.fetch_add(1, Ordering::Relaxed);
    }
    result
}
unsafe extern "C" fn cpu(
    handle: *mut c_void,
    meta: *const c_void,
    info: f::encoder_frame_info,
    y: f::encoder_plane,
    u: f::encoder_plane,
    v: f::encoder_plane,
) -> bool {
    let Some(entry) = encoders().get(handle as usize) else {
        if pointer_reserved(handle as usize) {
            return false;
        };
        // SAFETY: foreign handles retain the original by-value plane ABI.
        return HOOKS
            .get()
            .is_some_and(|hooks| unsafe { (hooks.cpu)(handle, meta, info, y, u, v) });
    };
    let Some(_operation) = entry.admission.enter() else {
        return false;
    };
    let planes = [y, u, v];
    // SAFETY: caller supplies valid plane borrows and meta; the private API
    // validates dimensions and consumes no pixel data after this call returns.
    let result = unsafe {
        entry.backend.api.i420.unwrap()(
            entry.handle as *mut c_void,
            meta.cast(),
            info,
            planes.as_ptr(),
        )
    };
    counts(entry.codec).cpu.fetch_add(1, Ordering::Relaxed);
    if !result {
        counts(entry.codec).failed.fetch_add(1, Ordering::Relaxed);
    }
    result
}
impl Backend {
    unsafe fn load(image: *const u8, size: usize) -> Result<Option<Self>, ()> {
        let mut backing = None;
        let path = if let Some(path) =
            std::env::var_os("DPE_HEVC_BACKEND").filter(|path| !path.is_empty())
        {
            use std::os::unix::ffi::OsStrExt;
            CString::new(path.as_os_str().as_bytes()).map_err(|_| ())?
        } else {
            if image.is_null() && size == 0 {
                return Ok(None);
            };
            if image.is_null() || size == 0 || size > 16 * 1024 * 1024 {
                return Err(());
            };
            // SAFETY: memfd_create creates a new private, sealable file descriptor.
            let fd = unsafe {
                libc::memfd_create(
                    c"dpe-hevc".as_ptr(),
                    libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING,
                )
            };
            if fd < 0 {
                return Err(());
            };
            // SAFETY: this successful syscall transferred one fresh descriptor.
            let mut file = unsafe { File::from_raw_fd(fd) };
            // SAFETY: the addon supplies immutable linked image bytes with this bound.
            file.write_all(unsafe { std::slice::from_raw_parts(image, size) })
                .map_err(|_| ())?;
            // SAFETY: this private file has no writable mappings; prohibit all mutation.
            if unsafe {
                libc::fcntl(
                    fd,
                    libc::F_ADD_SEALS,
                    libc::F_SEAL_WRITE
                        | libc::F_SEAL_GROW
                        | libc::F_SEAL_SHRINK
                        | libc::F_SEAL_SEAL,
                )
            } < 0
            {
                return Err(());
            };
            let path =
                CString::new(format!("/proc/self/fd/{}", file.as_raw_fd())).map_err(|_| ())?;
            backing = Some(file);
            path
        };
        // SAFETY: dlopen borrows the terminated path; backing pins its fd until return.
        let raw = unsafe { libc::dlopen(path.as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) };
        drop(backing);
        if raw.is_null() {
            return Err(());
        };
        let library = Library(raw);
        macro_rules! symbol {
            ($name:literal,$ty:ty) => {{
                // SAFETY: the private library's versioned ABI fixes this symbol signature.
                let raw = unsafe { libc::dlsym(library.0, $name.as_ptr()) };
                // SAFETY: POSIX function pointers match dlsym pointer representation.
                (!raw.is_null()).then(|| unsafe { std::mem::transmute::<*mut c_void, $ty>(raw) })
            }};
        }
        let get = symbol!(
            c"dpe_hevc_api",
            unsafe extern "C" fn() -> *const f::dpe_hevc_api
        )
        .ok_or(())?;
        // SAFETY: the private getter returns an immutable table while this library lives.
        let api = unsafe { get().as_ref() }.ok_or(())?;
        if api.abi != f::DPE_HEVC_ABI
            || api.size as usize != size_of::<f::dpe_hevc_api>()
            || api.probe.is_none()
            || api.create.is_none()
            || api.update.is_none()
            || api.i420.is_none()
            || api.nv12.is_none()
            || api.destroy.is_none()
            || api.vendor.is_none()
        {
            return Err(());
        };
        Ok(Some(Self {
            api: *api,
            imports: symbol!(c"dpe_encoder_import_stats", unsafe extern "C" fn(*mut u64)),
            scope: symbol!(c"dpe_encoder_hardware_scope", Vendor),
            profile: symbol!(
                c"dpe_encoder_profile_stage",
                unsafe extern "C" fn(u32, *mut u64) -> bool
            ),
            _library: library,
        }))
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_hevc_install(
    filename: *const c_char,
    offsets: *const u32,
    image: *const u8,
    image_size: usize,
) -> bool {
    if filename.is_null() || offsets.is_null() {
        return false;
    };
    let _install = lock(&INSTALL);
    // SAFETY: the native addon supplies one terminated filename and 19 reviewed offsets.
    let (filename, offsets) = unsafe {
        (
            CStr::from_ptr(filename),
            std::slice::from_raw_parts(offsets, 19),
        )
    };
    let Ok(module) = Module::loaded(filename) else {
        return false;
    };
    let base = module.base;
    let installed = INSTALLED.load(Ordering::Acquire);
    if installed != 0 {
        return installed == base;
    };
    if HOOKS.get().is_some_and(|hooks| hooks.module.base != base) {
        return false;
    };
    let names = [
        c"cc_encoder_add_frame_native",
        c"cc_encoder_add_frame_i420",
        c"cc_encoder_supports_recon_frame",
        c"cc_encoder_vendor",
        c"cc_probe_gpu_encoder",
    ];
    let replacements = [
        native as *const () as usize,
        cpu as *const () as usize,
        recon as *const () as usize,
        vendor as *const () as usize,
        probe as *const () as usize,
    ];
    let mut targets = [0; 9];
    let mut slots = Vec::with_capacity(5);
    for i in 0..5 {
        let Ok(address) = module.address(offsets[3 * i]) else {
            return false;
        };
        let Ok(target) = module.address(offsets[3 * i + 1]) else {
            return false;
        };
        if module.symbol(names[i]) != target {
            return false;
        };
        targets[i] = target;
        let lazy = if offsets[3 * i + 2] == 0 {
            None
        } else {
            let Ok(lazy) = module.address(offsets[3 * i + 2]) else {
                return false;
            };
            if !module.executable(lazy, 1) {
                return false;
            };
            Some(lazy)
        };
        slots.push(Slot {
            address,
            target,
            lazy,
            replacement: replacements[i],
        });
    }
    for (i, fragment) in [
        c"19PooledVulkanSurface10descriptor",
        c"13capture_linux10LinuxFrameE9drop_slow",
    ]
    .into_iter()
    .enumerate()
    {
        let Ok(target) = module.address(offsets[15 + i]) else {
            return false;
        };
        if !module.named_function(target, fragment) {
            return false;
        };
        targets[5 + i] = target;
    }
    for (i, name) in [c"cc_video_frame_clone_i420", c"cc_i420_buffer_destroy"]
        .into_iter()
        .enumerate()
    {
        let Ok(target) = module.address(offsets[17 + i]) else {
            return false;
        };
        if module.symbol(name) != target || !module.executable(target, 1) {
            return false;
        };
        targets[7 + i] = target;
    }
    let Ok(patches) = Slots::prepare(&module, &slots) else {
        return false;
    };
    if BACKEND.get().is_none() {
        // SAFETY: these are the immutable embedded-image bytes supplied by the addon.
        match unsafe { Backend::load(image, image_size) } {
            Ok(Some(backend)) => {
                if BACKEND.set(backend).is_err() {
                    return false;
                };
            }
            Ok(None) => return true,
            Err(()) => {
                eprintln!("DPE HEVC backend unavailable; keeping native codec capabilities.");
                return true;
            }
        }
    }
    if HOOKS.get().is_none() {
        // SAFETY: each target was matched against its exact reviewed C symbol or
        // native ownership accessor, with executable bounds and exact start checked.
        let hooks = unsafe {
            Hooks {
                module,
                native: std::mem::transmute::<usize, Native>(targets[0]),
                cpu: std::mem::transmute::<usize, Cpu>(targets[1]),
                recon: std::mem::transmute::<usize, Recon>(targets[2]),
                vendor: std::mem::transmute::<usize, Vendor>(targets[3]),
                probe: std::mem::transmute::<usize, Probe>(targets[4]),
                describe: std::mem::transmute::<usize, Describe>(targets[5]),
                drop_linux: std::mem::transmute::<usize, DropLinux>(targets[6]),
                clone_cpu: std::mem::transmute::<usize, CloneCpu>(targets[7]),
                drop_cpu: std::mem::transmute::<usize, DropCpu>(targets[8]),
            }
        };
        if HOOKS.set(hooks).is_err() {
            return false;
        };
    }
    // SAFETY: the module, backend and original callbacks are now retained for
    // process lifetime, including rollback failure. Startup is serialized above.
    if unsafe { patches.apply() }.is_err() {
        return false;
    };
    INSTALLED.store(base, Ordering::Release);
    true
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_encoded_inputs() -> u64 {
    DIRECT.load(Ordering::Relaxed)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_encoder_import_stats(output: *mut u64) {
    if output.is_null() {
        return;
    };
    let mut values = [0; 5];
    if let Some(imports) = BACKEND.get().and_then(|backend| backend.imports) {
        // SAFETY: the private accessor writes five u64 counters.
        unsafe { imports(values.as_mut_ptr()) };
    }
    // SAFETY: caller supplies five writable u64 output counters.
    unsafe { ptr::copy_nonoverlapping(values.as_ptr(), output, 5) };
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_encoder_profile(stage: u32, output: *mut u64) -> bool {
    if output.is_null() {
        return false;
    };
    let mut values = [0; 4];
    let result = BACKEND
        .get()
        .and_then(|backend| backend.profile)
        .is_some_and(|profile| {
            // SAFETY: the private profiler writes four u64 counters.
            unsafe { profile(stage, values.as_mut_ptr()) }
        });
    // SAFETY: caller supplies four writable u64 output counters.
    unsafe { ptr::copy_nonoverlapping(values.as_ptr(), output, 4) };
    result
}
unsafe fn stats(codec: u32, output: *mut u64) {
    if output.is_null() {
        return;
    };
    let counts = counts(codec);
    let values = [
        u64::from(INSTALLED.load(Ordering::Acquire) != 0),
        if codec == 1 {
            PROBES.load(Ordering::Relaxed)
        } else {
            u64::from(AV1_SCOPES.load(Ordering::Relaxed) == 1)
        },
        counts.gpu.load(Ordering::Relaxed),
        counts.cpu.load(Ordering::Relaxed),
        counts.fallback.load(Ordering::Relaxed),
        counts.failed.load(Ordering::Relaxed),
    ];
    // SAFETY: caller supplies six writable u64 counters.
    unsafe { ptr::copy_nonoverlapping(values.as_ptr(), output, 6) };
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_hevc_stats(output: *mut u64) {
    // SAFETY: forwards the declared six-counter output contract.
    unsafe { stats(1, output) };
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_av1_stats(output: *mut u64) {
    // SAFETY: forwards the declared six-counter output contract.
    unsafe { stats(2, output) };
}
