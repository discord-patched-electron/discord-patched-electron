// SPDX-License-Identifier: MIT
//! Reviewed native C exports forward foreign frames unchanged. Owned handles
//! are resolved through Rust tables; no upstream Rust object is constructed.
use crate::{
    direct_gpu as gpu, ffi,
    node::{self, Env, Info, Value},
    worker,
};
use dpe_core::handles::pointer_reserved;
use std::{
    ffi::{CStr, c_void},
    fmt::Write,
    ptr,
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};
type Destroy = unsafe extern "C" fn(*mut c_void);
type Data = unsafe extern "C" fn(*const c_void, *mut usize, *mut usize, *mut u64);
type Planar = unsafe extern "C" fn(
    *mut c_void,
    *mut *const u8,
    *mut usize,
    *mut usize,
    *mut usize,
    *mut usize,
    *mut usize,
) -> *mut c_void;
type Nv12 =
    unsafe extern "C" fn(*mut c_void, *mut *const u8, *mut usize, *mut usize, *mut usize) -> bool;
type Abgr = unsafe extern "C" fn(*mut c_void, *mut *const u8, *mut usize) -> bool;
type Has = unsafe extern "C" fn(*const c_void) -> bool;
type Rendition = unsafe extern "C" fn(*mut c_void, usize, usize) -> *mut c_void;
type Renditions = unsafe extern "C" fn(*mut c_void, *const c_void, usize) -> i32;
struct Original {
    destroy: Destroy,
    data: Data,
    planar: Planar,
    planar_destroy: Destroy,
    nv12: Nv12,
    abgr: Abgr,
    cpu: Has,
    gpu: Has,
    rendition: Rendition,
    renditions: Renditions,
}
static ORIGINAL: OnceLock<Original> = OnceLock::new();
static READY: AtomicBool = AtomicBool::new(false);
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
static INSTALL: Mutex<()> = Mutex::new(());
unsafe extern "C" {
    fn dpe_direct_encoded_inputs() -> u64;
    fn dpe_encoder_import_stats(values: *mut u64);
    fn dpe_encoder_profile(stage: u32, values: *mut u64) -> bool;
    fn dpe_rust_preview_stats(values: *mut u64);
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_capture_ready() -> bool {
    READY.load(Ordering::Acquire)
}
unsafe extern "C" fn destroy(frame: *mut c_void) {
    if !gpu::dpe_direct_frame_destroy(frame)
        && !pointer_reserved(frame as usize)
        && let Some(original) = ORIGINAL.get()
    {
        // SAFETY: unowned frame follows the unchanged native destroy ABI.
        unsafe {
            (original.destroy)(frame);
        }
    }
}
unsafe extern "C" fn data(frame: *const c_void, w: *mut usize, h: *mut usize, time: *mut u64) {
    // SAFETY: wrapper forwards the caller's writable native data outputs.
    if unsafe { gpu::dpe_direct_frame_data(frame, w, h, time) } {
        return;
    }
    if pointer_reserved(frame as usize) {
        // SAFETY: optional native scalar outputs are writable when supplied.
        unsafe {
            if !w.is_null() {
                w.write(0);
            }
            if !h.is_null() {
                h.write(0);
            }
            if !time.is_null() {
                time.write(0);
            }
        }
        return;
    }
    if let Some(original) = ORIGINAL.get() {
        // SAFETY: foreign frame and original C arguments are unchanged.
        unsafe {
            (original.data)(frame, w, h, time);
        }
    }
}
unsafe extern "C" fn planar(
    frame: *mut c_void,
    pixels: *mut *const u8,
    size: *mut usize,
    sy: *mut usize,
    suv: *mut usize,
    u: *mut usize,
    v: *mut usize,
) -> *mut c_void {
    if !pointer_reserved(frame as usize) {
        // SAFETY: foreign frames retain their exact original clone ABI.
        return ORIGINAL.get().map_or(ptr::null_mut(), |original| unsafe {
            (original.planar)(frame, pixels, size, sy, suv, u, v)
        });
    }
    if pixels.is_null()
        || size.is_null()
        || sy.is_null()
        || suv.is_null()
        || u.is_null()
        || v.is_null()
    {
        return ptr::null_mut();
    }
    // SAFETY: native caller provides these writable scalar outputs.
    unsafe {
        *pixels = ptr::null();
        *size = 0;
        *sy = 0;
        *suv = 0;
        *u = 0;
        *v = 0;
    }
    if worker::dpe_direct_capture_in_delivery() {
        return ptr::null_mut();
    }
    // SAFETY: owned frame lookup plus the caller's validated output pointers.
    unsafe { gpu::dpe_direct_frame_i420(frame, pixels, size, sy, suv, u, v) }
}
unsafe extern "C" fn planar_destroy(lease: *mut c_void) {
    if !gpu::dpe_direct_i420_destroy(lease)
        && !pointer_reserved(lease as usize)
        && let Some(original) = ORIGINAL.get()
    {
        // SAFETY: unowned CPU lease retains its original destructor.
        unsafe {
            (original.planar_destroy)(lease);
        }
    }
}
unsafe extern "C" fn nv12(
    frame: *mut c_void,
    pixels: *mut *const u8,
    size: *mut usize,
    sy: *mut usize,
    suv: *mut usize,
) -> bool {
    if !pointer_reserved(frame as usize) {
        // SAFETY: foreign frame and output arguments retain their native ABI.
        return ORIGINAL
            .get()
            .is_some_and(|original| unsafe { (original.nv12)(frame, pixels, size, sy, suv) });
    }
    if pixels.is_null() || size.is_null() || sy.is_null() || suv.is_null() {
        return false;
    }
    // SAFETY: caller provides these writable native outputs.
    unsafe {
        *pixels = ptr::null();
        *size = 0;
        *sy = 0;
        *suv = 0;
    }
    // SAFETY: Rust handle lookup and validated C output pointers; delivery never maps source pixels.
    !worker::dpe_direct_capture_in_delivery()
        && unsafe { gpu::dpe_direct_frame_cpu_nv12(frame, pixels, size, sy, suv) }
}
unsafe extern "C" fn abgr(frame: *mut c_void, pixels: *mut *const u8, size: *mut usize) -> bool {
    if !pointer_reserved(frame as usize) {
        // SAFETY: foreign frame/output arguments retain their native ABI.
        return ORIGINAL
            .get()
            .is_some_and(|original| unsafe { (original.abgr)(frame, pixels, size) });
    }
    if !pixels.is_null() && !size.is_null() {
        // SAFETY: native caller provides writable outputs.
        unsafe {
            *pixels = ptr::null();
            *size = 0;
        }
    }
    false
}
unsafe extern "C" fn has_cpu(frame: *const c_void) -> bool {
    // SAFETY: only unowned frames reach their original accessor.
    !pointer_reserved(frame as usize)
        && ORIGINAL
            .get()
            .is_some_and(|original| unsafe { (original.cpu)(frame) })
}
unsafe extern "C" fn has_gpu(frame: *const c_void) -> bool {
    // SAFETY: only unowned frames reach their original accessor.
    gpu::dpe_direct_frame_owned(frame)
        || (!pointer_reserved(frame as usize)
            && ORIGINAL
                .get()
                .is_some_and(|original| unsafe { (original.gpu)(frame) }))
}
unsafe extern "C" fn rendition(frame: *mut c_void, w: usize, h: usize) -> *mut c_void {
    if pointer_reserved(frame as usize) {
        return ptr::null_mut();
    }
    // SAFETY: foreign frame and rendition dimensions follow the original ABI.
    ORIGINAL.get().map_or(ptr::null_mut(), |original| unsafe {
        (original.rendition)(frame, w, h)
    })
}
unsafe extern "C" fn renditions(capture: *mut c_void, wanted: *const c_void, count: usize) -> i32 {
    if pointer_reserved(capture as usize) {
        return 0;
    }
    // SAFETY: foreign capturer and its native request array are forwarded unchanged.
    ORIGINAL.get().map_or(-1, |original| unsafe {
        (original.renditions)(capture, wanted, count)
    })
}
unsafe fn install(env: Env, info: Info) -> Result<bool, &'static CStr> {
    let _install = INSTALL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let api = node::api().ok_or(c"Node API unavailable")?;
    let mut args = [ptr::null_mut(); 21];
    let mut count = args.len();
    // SAFETY: Node owns env/info; stack argument output has the declared capacity.
    if unsafe {
        (api.info)(
            env,
            info,
            &mut count,
            args.as_mut_ptr(),
            ptr::null_mut(),
            ptr::null_mut(),
        )
    } != 0
        || count != args.len()
    {
        return Err(c"Invalid direct capture linkage");
    }
    let mut filename = [0u8; 4096];
    let mut length = 0;
    // SAFETY: bounded UTF-8 output buffer, converted through the public Node API.
    if unsafe {
        (api.string)(
            env,
            args[0],
            filename.as_mut_ptr().cast(),
            filename.len(),
            &mut length,
        )
    } != 0
        || length == 0
        || length >= filename.len() - 1
        || filename[..length].contains(&0)
    {
        return Err(c"Invalid direct capture module");
    }
    let mut offsets = [0u32; 20];
    for (offset, arg) in offsets.iter_mut().zip(&args[1..]) {
        // SAFETY: live Node values are converted only through Node's integer API.
        if unsafe { (api.uint)(env, *arg, offset) } != 0 || *offset == 0 {
            return Err(c"Invalid direct capture offset");
        }
    }
    // SAFETY: Node NUL-terminates filename; accept only an already loaded module.
    let raw = unsafe { libc::dlopen(filename.as_ptr().cast(), libc::RTLD_NOW | libc::RTLD_NOLOAD) };
    if raw.is_null() {
        return Err(c"Direct capture module is not loaded");
    }
    let module = node::Library(raw);
    let mut map: *mut ffi::link_map = ptr::null_mut();
    // SAFETY: this live dlopen reference owns the public ELF link_map output.
    if unsafe { libc::dlinfo(raw, libc::RTLD_DI_LINKMAP, (&raw mut map).cast()) } != 0
        || map.is_null()
    {
        return Err(c"Direct capture module map unavailable");
    }
    // SAFETY: successful dlinfo establishes this loaded module's public link_map.
    let base = unsafe { (*map).l_addr } as usize;
    if INSTALLED.load(Ordering::Acquire) != 0 {
        return if INSTALLED.load(Ordering::Acquire) == base {
            Ok(dpe_direct_capture_ready())
        } else {
            Err(c"Direct capture module changed")
        };
    }
    let names = [
        c"cc_video_frame_destroy",
        c"cc_video_frame_get_data",
        c"cc_video_frame_clone_i420",
        c"cc_i420_buffer_destroy",
        c"cc_video_frame_get_nv12_data",
        c"cc_video_frame_get_abgr_data",
        c"cc_video_frame_has_cpu_frame",
        c"cc_video_frame_has_gpu_frame",
        c"cc_video_frame_get_rendition",
        c"cc_linux_capture_set_requested_renditions",
    ];
    let mut slots = [0usize; 10];
    let mut targets = [0usize; 10];
    let mut pages = Vec::with_capacity(10);
    // SAFETY: scalar sysconf query, no borrowed pointers.
    let page = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if page <= 0 {
        return Err(c"Invalid page size");
    }
    let page = page as usize;
    for i in 0..10 {
        slots[i] = base
            .checked_add(offsets[2 * i] as usize)
            .ok_or(c"Direct capture address overflow")?;
        targets[i] = base
            .checked_add(offsets[2 * i + 1] as usize)
            .ok_or(c"Direct capture address overflow")?;
        // SAFETY: compiled guard validates ELF permissions, pointer alignment,
        // relocation target or the canonical lazy PLT stub before reading it.
        let protection =
            crate::picker_install::dpe_capture_picker_linkage(base, slots[i], targets[i]);
        // SAFETY: initialized module handle and static NUL-terminated export name.
        if protection == 0 || unsafe { libc::dlsym(raw, names[i].as_ptr()) } as usize != targets[i]
        {
            return Err(c"Direct capture export differs from review");
        }
        let address = slots[i] / page * page;
        if !pages.iter().any(|&(existing, _)| existing == address) {
            pages.push((address, protection));
        }
    }
    const GET_DATA: &[u8] = &[
        0x48, 0x8b, 0x47, 0x10, 0x48, 0x89, 0x06, 0x48, 0x8b, 0x47, 0x18, 0x48, 0x89, 0x02, 0x48,
        0x8b, 0x47, 0x20, 0x48, 0x89, 0x01, 0xc3,
    ];
    // SAFETY: reviewed executable exports have these lengths, independently
    // checked by scope generation and the whole prepared-module hash in JS.
    let reviewed = unsafe {
        std::slice::from_raw_parts(targets[1] as *const u8, GET_DATA.len()) == GET_DATA
            && std::slice::from_raw_parts(targets[6] as *const u8, 3) == [0xb0, 0x01, 0xc3]
            && std::slice::from_raw_parts(targets[8] as *const u8, 3) == [0x31, 0xc0, 0xc3]
    };
    if !reviewed {
        return Err(c"Direct capture frame ABI differs from review");
    }
    for (i, &(address, protection)) in pages.iter().enumerate() {
        // SAFETY: only reviewed GOT pages are temporarily writable during startup.
        if unsafe { libc::mprotect(address as *mut c_void, page, protection | libc::PROT_WRITE) }
            != 0
        {
            for &(address, protection) in &pages[..i] {
                // SAFETY: restore only pages whose protection we changed above.
                unsafe {
                    libc::mprotect(address as *mut c_void, page, protection);
                }
            }
            return Err(c"Cannot prepare direct capture linkage");
        }
    }
    // SAFETY: all ten symbol names, targets, relocation slots and relevant
    // accessors were verified above against the exact reviewed C ABI.
    let original = unsafe {
        Original {
            destroy: std::mem::transmute::<usize, Destroy>(targets[0]),
            data: std::mem::transmute::<usize, Data>(targets[1]),
            planar: std::mem::transmute::<usize, Planar>(targets[2]),
            planar_destroy: std::mem::transmute::<usize, Destroy>(targets[3]),
            nv12: std::mem::transmute::<usize, Nv12>(targets[4]),
            abgr: std::mem::transmute::<usize, Abgr>(targets[5]),
            cpu: std::mem::transmute::<usize, Has>(targets[6]),
            gpu: std::mem::transmute::<usize, Has>(targets[7]),
            rendition: std::mem::transmute::<usize, Rendition>(targets[8]),
            renditions: std::mem::transmute::<usize, Renditions>(targets[9]),
        }
    };
    let _ = ORIGINAL.set(original);
    let replacements = [
        destroy as *const () as usize,
        data as *const () as usize,
        planar as *const () as usize,
        planar_destroy as *const () as usize,
        nv12 as *const () as usize,
        abgr as *const () as usize,
        has_cpu as *const () as usize,
        has_gpu as *const () as usize,
        rendition as *const () as usize,
        renditions as *const () as usize,
    ];
    for (&slot, &replacement) in slots.iter().zip(&replacements) {
        // SAFETY: aligned validated native pointer slot; original state is
        // published before callbacks can observe the replacement.
        unsafe {
            (*(slot as *const AtomicUsize)).store(replacement, Ordering::Release);
        }
    }
    let mut restored = true;
    for &(address, protection) in &pages {
        // SAFETY: restore the original ELF permissions for every changed page.
        restored &= unsafe { libc::mprotect(address as *mut c_void, page, protection) } == 0;
    }
    // A restoration failure leaves only forwarding wrappers. No direct source
    // can start until READY is published, and the original module stays loaded.
    std::mem::forget(module);
    INSTALLED.store(base, Ordering::Release);
    READY.store(restored, Ordering::Release);
    Ok(restored)
}
unsafe extern "C" fn install_callback(env: Env, info: Info) -> Value {
    // SAFETY: Node supplies a live callback environment and arguments.
    let result = std::panic::catch_unwind(|| unsafe { install(env, info) });
    match result {
        Ok(Ok(flag)) => {
            let mut out = ptr::null_mut();
            if let Some(api) = node::api() {
                // SAFETY: live Node environment and writable result value.
                unsafe {
                    (api.boolean)(env, flag, &mut out);
                }
            }
            out
        }
        Ok(Err(message)) => node::failure(env, message),
        Err(_) => node::failure(env, c"Direct capture installation failed"),
    }
}
unsafe extern "C" fn stats_callback(env: Env, _info: Info) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    let mut values = [0u64; 5];
    let mut imports = [0u64; 5];
    let mut preview = [0u64; 5];
    // SAFETY: each reviewed counter function receives five writable elements.
    unsafe {
        worker::dpe_direct_capture_stats(values.as_mut_ptr());
        dpe_encoder_import_stats(imports.as_mut_ptr());
        dpe_rust_preview_stats(preview.as_mut_ptr());
    }
    let mut encoded = String::with_capacity(4096);
    // SAFETY: this encoder counter has no pointer arguments or retained resources.
    let inputs = unsafe { dpe_direct_encoded_inputs() };
    let names = [
        "activeCaptures",
        "convertedFrames",
        "droppedFrames",
        "failures",
        "receivedFrames",
        "cpuReadbacks",
        "gpuEncoderInputs",
        "encoderImports",
        "encoderImportHits",
        "encoderImportEvictions",
        "encoderImportsLive",
        "encoderImportBypasses",
        "previewRequests",
        "previewFrames",
        "previewReadbackBytes",
        "previewDropped",
        "previewErrors",
    ];
    let counters = values
        .into_iter()
        .chain([gpu::dpe_direct_gpu_readbacks(), inputs])
        .chain(imports)
        .chain(preview);
    encoded.push_str("{\"transport\":\"direct-gpu\"");
    for (name, count) in names.iter().zip(counters) {
        let _ = write!(encoded, ",\"{name}\":{count}");
    }
    encoded.push_str(",\"profile\":[");
    for stage in 0..32 {
        let mut own = [0u64; 4];
        let mut encoder = [0u64; 4];
        // SAFETY: each profiling function writes exactly four counter elements.
        unsafe {
            dpe_os::profiler::dpe_profile_stage(stage, own.as_mut_ptr());
            dpe_encoder_profile(stage, encoder.as_mut_ptr());
        }
        if stage > 0 {
            encoded.push(',');
        }
        let _ = write!(
            encoded,
            "[{},{},{},{}]",
            own[0].saturating_add(encoder[0]),
            own[1].saturating_add(encoder[1]),
            own[2].saturating_add(encoder[2]),
            own[3].max(encoder[3])
        );
    }
    encoded.push_str("]}");
    let mut out = ptr::null_mut();
    // SAFETY: Node copies exactly encoded.len() UTF-8 bytes before this String drops.
    if unsafe { (api.new_string)(env, encoded.as_ptr().cast(), encoded.len(), &mut out) } == 0 {
        out
    } else {
        ptr::null_mut()
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_capture_register(env: Env, exports: Value) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    for (name, callback) in [
        (
            c"installCaptureDirect",
            install_callback as unsafe extern "C" fn(Env, Info) -> Value,
        ),
        (c"getCaptureDirectStats", stats_callback),
    ] {
        let mut function = ptr::null_mut();
        // SAFETY: live Node env/exports; static callbacks and names live for this module's lifetime.
        if unsafe {
            (api.function)(
                env,
                name.as_ptr(),
                usize::MAX,
                Some(callback),
                ptr::null_mut(),
                &mut function,
            )
        } != 0
            // SAFETY: function is the value just created in the same Node environment.
            || unsafe { (api.property)(env, exports, name.as_ptr(), function) } != 0
        {
            return ptr::null_mut();
        }
    }
    exports
}

#[cfg(test)]
mod tests {
    use super::*;
    use dpe_core::handles::PointerKind;
    use std::sync::atomic::AtomicU64;
    static CALLS: AtomicU64 = AtomicU64::new(0);
    unsafe extern "C" fn native_destroy(_: *mut c_void) {
        CALLS.fetch_add(1, Ordering::Relaxed);
    }
    unsafe extern "C" fn native_data(_: *const c_void, _: *mut usize, _: *mut usize, _: *mut u64) {
        CALLS.fetch_add(1, Ordering::Relaxed);
    }
    unsafe extern "C" fn native_planar(
        _: *mut c_void,
        _: *mut *const u8,
        _: *mut usize,
        _: *mut usize,
        _: *mut usize,
        _: *mut usize,
        _: *mut usize,
    ) -> *mut c_void {
        CALLS.fetch_add(1, Ordering::Relaxed);
        ptr::null_mut()
    }
    unsafe extern "C" fn native_nv12(
        _: *mut c_void,
        _: *mut *const u8,
        _: *mut usize,
        _: *mut usize,
        _: *mut usize,
    ) -> bool {
        CALLS.fetch_add(1, Ordering::Relaxed);
        true
    }
    unsafe extern "C" fn native_abgr(_: *mut c_void, _: *mut *const u8, _: *mut usize) -> bool {
        CALLS.fetch_add(1, Ordering::Relaxed);
        true
    }
    unsafe extern "C" fn native_has(_: *const c_void) -> bool {
        CALLS.fetch_add(1, Ordering::Relaxed);
        true
    }
    unsafe extern "C" fn native_rendition(_: *mut c_void, _: usize, _: usize) -> *mut c_void {
        CALLS.fetch_add(1, Ordering::Relaxed);
        ptr::null_mut()
    }
    unsafe extern "C" fn native_renditions(_: *mut c_void, _: *const c_void, _: usize) -> i32 {
        CALLS.fetch_add(1, Ordering::Relaxed);
        7
    }
    #[test]
    fn retired_pointer_operations_never_reach_foreign_object_accessors() {
        assert!(
            ORIGINAL
                .set(Original {
                    destroy: native_destroy,
                    data: native_data,
                    planar: native_planar,
                    planar_destroy: native_destroy,
                    nv12: native_nv12,
                    abgr: native_abgr,
                    cpu: native_has,
                    gpu: native_has,
                    rendition: native_rendition,
                    renditions: native_renditions
                })
                .is_ok()
        );
        let serial = PointerKind::maximum_serial();
        let frame = PointerKind::VideoFrame.token(serial).unwrap().get() as *mut c_void;
        let lease = PointerKind::Planar.token(serial).unwrap().get() as *mut c_void;
        let capture = PointerKind::Capture.token(serial).unwrap().get() as *mut c_void;
        let (mut w, mut h, mut time) = (99usize, 99usize, 99u64);
        let (mut pixels, mut size, mut sy, mut suv, mut u, mut v) =
            (ptr::dangling::<u8>(), 99, 99, 99, 99, 99);
        // SAFETY: reserved tokens are never dereferenced. All output arguments
        // point to live scalars; foreign controls are consumed only by mocks.
        unsafe {
            destroy(frame);
            planar_destroy(lease);
            data(frame, &mut w, &mut h, &mut time);
            assert_eq!((w, h, time), (0, 0, 0));
            assert!(
                planar(
                    frame,
                    &mut pixels,
                    &mut size,
                    &mut sy,
                    &mut suv,
                    &mut u,
                    &mut v
                )
                .is_null()
            );
            assert!(pixels.is_null());
            assert_eq!((size, sy, suv, u, v), (0, 0, 0, 0, 0));
            assert!(!nv12(frame, &mut pixels, &mut size, &mut sy, &mut suv));
            assert!(!abgr(frame, &mut pixels, &mut size));
            assert!(!has_cpu(frame));
            assert!(!has_gpu(frame));
            assert!(rendition(frame, 16, 16).is_null());
            assert_eq!(renditions(capture, ptr::null(), 0), 0);
            assert_eq!(CALLS.load(Ordering::Relaxed), 0);
            let foreign = ptr::dangling_mut::<u64>().cast::<c_void>();
            destroy(foreign);
            assert!(has_gpu(foreign));
            assert_eq!(CALLS.load(Ordering::Relaxed), 2);
        }
    }
}
