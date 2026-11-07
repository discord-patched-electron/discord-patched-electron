// SPDX-License-Identifier: MIT
//! Small Node/ELF boundary. All queueing and GPU ownership live behind safe types.
use crate::{ffi, preview};
use std::{
    ffi::{CStr, c_char, c_void},
    ptr,
    sync::{
        OnceLock,
        atomic::{AtomicUsize, Ordering},
    },
};
pub(crate) type Env = ffi::napi_env;
pub(crate) type Value = ffi::napi_value;
pub(crate) type Info = ffi::napi_callback_info;
pub(crate) struct Api {
    pub(crate) info: unsafe extern "C" fn(
        Env,
        Info,
        *mut usize,
        *mut Value,
        *mut Value,
        *mut *mut c_void,
    ) -> i32,
    pub(crate) string: unsafe extern "C" fn(Env, Value, *mut c_char, usize, *mut usize) -> i32,
    pub(crate) new_string: unsafe extern "C" fn(Env, *const c_char, usize, *mut Value) -> i32,
    pub(crate) uint: unsafe extern "C" fn(Env, Value, *mut u32) -> i32,
    pub(crate) get_bool: unsafe extern "C" fn(Env, Value, *mut bool) -> i32,
    pub(crate) double: unsafe extern "C" fn(Env, Value, *mut f64) -> i32,
    pub(crate) undefined: unsafe extern "C" fn(Env, *mut Value) -> i32,
    pub(crate) boolean: unsafe extern "C" fn(Env, bool, *mut Value) -> i32,
    pub(crate) function: unsafe extern "C" fn(
        Env,
        *const c_char,
        usize,
        Option<unsafe extern "C" fn(Env, Info) -> Value>,
        *mut c_void,
        *mut Value,
    ) -> i32,
    pub(crate) property: unsafe extern "C" fn(Env, Value, *const c_char, Value) -> i32,
    pub(crate) error: unsafe extern "C" fn(Env, *const c_char, *const c_char) -> i32,
    pub(crate) cleanup:
        unsafe extern "C" fn(Env, Option<unsafe extern "C" fn(*mut c_void)>, *mut c_void) -> i32,
}
static API: OnceLock<Api> = OnceLock::new();
pub(crate) fn api() -> Option<&'static Api> {
    if API.get().is_none() {
        let _ = API.set(Api::load()?);
    }
    API.get()
}
static ENVS: OnceLock<std::sync::Mutex<std::collections::HashSet<usize>>> = OnceLock::new();
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
impl Api {
    fn load() -> Option<Self> {
        macro_rules! symbol {
            ($name:literal,$type:ty) => {{
                // SAFETY: lookup is limited to the exact Node-API function names/signatures below.
                let p = unsafe { libc::dlsym(libc::RTLD_DEFAULT, $name.as_ptr()) };
                if p.is_null() {
                    return None;
                }
                // SAFETY: the pinned Node-API C header defines this function's ABI.
                unsafe { std::mem::transmute::<*mut c_void, $type>(p) }
            }};
        }
        Some(Self {
            info: symbol!(
                c"napi_get_cb_info",
                unsafe extern "C" fn(
                    Env,
                    Info,
                    *mut usize,
                    *mut Value,
                    *mut Value,
                    *mut *mut c_void,
                ) -> i32
            ),
            string: symbol!(
                c"napi_get_value_string_utf8",
                unsafe extern "C" fn(Env, Value, *mut c_char, usize, *mut usize) -> i32
            ),
            new_string: symbol!(
                c"napi_create_string_utf8",
                unsafe extern "C" fn(Env, *const c_char, usize, *mut Value) -> i32
            ),
            uint: symbol!(
                c"napi_get_value_uint32",
                unsafe extern "C" fn(Env, Value, *mut u32) -> i32
            ),
            get_bool: symbol!(
                c"napi_get_value_bool",
                unsafe extern "C" fn(Env, Value, *mut bool) -> i32
            ),
            double: symbol!(
                c"napi_get_value_double",
                unsafe extern "C" fn(Env, Value, *mut f64) -> i32
            ),
            undefined: symbol!(
                c"napi_get_undefined",
                unsafe extern "C" fn(Env, *mut Value) -> i32
            ),
            boolean: symbol!(
                c"napi_get_boolean",
                unsafe extern "C" fn(Env, bool, *mut Value) -> i32
            ),
            function: symbol!(
                c"napi_create_function",
                unsafe extern "C" fn(
                    Env,
                    *const c_char,
                    usize,
                    Option<unsafe extern "C" fn(Env, Info) -> Value>,
                    *mut c_void,
                    *mut Value,
                ) -> i32
            ),
            property: symbol!(
                c"napi_set_named_property",
                unsafe extern "C" fn(Env, Value, *const c_char, Value) -> i32
            ),
            error: symbol!(
                c"napi_throw_error",
                unsafe extern "C" fn(Env, *const c_char, *const c_char) -> i32
            ),
            cleanup: symbol!(
                c"napi_add_env_cleanup_hook",
                unsafe extern "C" fn(
                    Env,
                    Option<unsafe extern "C" fn(*mut c_void)>,
                    *mut c_void,
                ) -> i32
            ),
        })
    }
}
pub(crate) fn failure(env: Env, message: &CStr) -> Value {
    if let Some(api) = API.get() {
        // SAFETY: called only by Node with a live env; static message is NUL-terminated.
        unsafe {
            (api.error)(env, ptr::null(), message.as_ptr());
        }
    }
    ptr::null_mut()
}
pub(crate) fn string_value(env: Env, text: &str) -> Value {
    let mut value = ptr::null_mut();
    if let Some(api) = api() {
        // SAFETY: internal callers are live Node callbacks; this bounded UTF-8
        // slice remains readable through the synchronous public API conversion.
        unsafe {
            (api.new_string)(env, text.as_ptr().cast(), text.len(), &mut value);
        }
    }
    value
}
/// Read a bounded library filename and exact scalar offsets from a Node call.
/// # Safety
/// Node supplies a live callback environment and info on this thread.
pub(crate) unsafe fn module_arguments<const N: usize>(
    env: Env,
    info: Info,
) -> Result<(std::ffi::CString, [u32; N]), &'static CStr> {
    // SAFETY: same live invocation, retaining the strict nonzero offset contract.
    unsafe { module_arguments_impl::<N>(env, info, false) }
}
/// # Safety
/// Same live callback contract; the encoder installer separately validates zero
/// sentinels for absent lazy bindings before any native address is accessed.
pub(crate) unsafe fn encoder_arguments<const N: usize>(
    env: Env,
    info: Info,
) -> Result<(std::ffi::CString, [u32; N]), &'static CStr> {
    // SAFETY: same bounded public Node conversions; no native dereference here.
    unsafe { module_arguments_impl::<N>(env, info, true) }
}
unsafe fn module_arguments_impl<const N: usize>(
    env: Env,
    info: Info,
    zeros: bool,
) -> Result<(std::ffi::CString, [u32; N]), &'static CStr> {
    let api = api().ok_or(c"Node API unavailable")?;
    let mut values = vec![ptr::null_mut(); N + 1];
    let mut count = values.len();
    // SAFETY: live Node call; output has exactly the declared value capacity.
    if unsafe {
        (api.info)(
            env,
            info,
            &mut count,
            values.as_mut_ptr(),
            ptr::null_mut(),
            ptr::null_mut(),
        )
    } != 0
        || count != values.len()
    {
        return Err(c"Invalid capture linkage arguments.");
    }
    let mut bytes = [0u8; 4096];
    let mut length = 0;
    // SAFETY: Node writes only into the bounded stack UTF-8 buffer.
    if unsafe {
        (api.string)(
            env,
            values[0],
            bytes.as_mut_ptr().cast(),
            bytes.len(),
            &mut length,
        )
    } != 0
        || length == 0
        || length >= bytes.len() - 1
    {
        return Err(c"Invalid capture module filename.");
    }
    let filename = std::ffi::CString::new(&bytes[..length])
        .map_err(|_| c"Invalid capture module filename.")?;
    let mut offsets = [0; N];
    for (&value, offset) in values[1..].iter().zip(&mut offsets) {
        // SAFETY: values belong to this live call; scalar output is writable.
        if unsafe { (api.uint)(env, value, offset) } != 0 || (!zeros && *offset == 0) {
            return Err(c"Invalid capture module offset.");
        }
    }
    Ok((filename, offsets))
}

/// # Safety
/// Node provides a live environment/exports; callbacks have static lifetimes.
pub(crate) unsafe fn register_functions(
    env: Env,
    exports: Value,
    functions: &[(&CStr, unsafe extern "C" fn(Env, Info) -> Value)],
) -> Value {
    let Some(api) = api() else {
        return ptr::null_mut();
    };
    for &(name, callback) in functions {
        let mut function = ptr::null_mut();
        // SAFETY: live Node values and static callback; Node owns the output.
        if unsafe { (api.function)(env, name.as_ptr(), usize::MAX, Some(callback), ptr::null_mut(), &mut function) } != 0
            // SAFETY: function was created in this same environment above.
            || unsafe { (api.property)(env, exports, name.as_ptr(), function) } != 0
        {
            return ptr::null_mut();
        }
    }
    exports
}
pub(crate) struct Library(pub(crate) *mut c_void);
// SAFETY: POSIX loader handles support concurrent symbol lookup and release.
// This guard owns a loader reference, never mutable library application state.
unsafe impl Send for Library {}
// SAFETY: shared access only uses thread-safe loader APIs; Drop requires ownership.
unsafe impl Sync for Library {}
impl Drop for Library {
    fn drop(&mut self) {
        // SAFETY: this guard owns one successful dlopen reference.
        unsafe {
            libc::dlclose(self.0);
        }
    }
}
pub(crate) fn permissions(address: usize) -> Option<i32> {
    permissions_range(address, 1)
}
pub(crate) fn permissions_range(address: usize, length: usize) -> Option<i32> {
    let end = address.checked_add(length)?;
    if length == 0 {
        return None;
    }
    std::fs::read_to_string("/proc/self/maps")
        .ok()?
        .lines()
        .find_map(|line| {
            let mut fields = line.split_whitespace();
            let (first, last) = fields.next()?.split_once('-')?;
            let (first, last) = (
                usize::from_str_radix(first, 16).ok()?,
                usize::from_str_radix(last, 16).ok()?,
            );
            if !(first <= address && end <= last) {
                return None;
            }
            let p = fields.next()?.as_bytes();
            if p.len() < 3 {
                return None;
            }
            Some(
                if p[0] == b'r' { libc::PROT_READ } else { 0 }
                    | if p[1] == b'w' { libc::PROT_WRITE } else { 0 }
                    | if p[2] == b'x' { libc::PROT_EXEC } else { 0 },
            )
        })
}
unsafe fn install(env: Env, info: Info) -> std::result::Result<bool, &'static CStr> {
    let api = API.get().ok_or(c"Node API unavailable")?;
    let mut args = [ptr::null_mut(); 6];
    let mut count = args.len();
    let mut filename = [0u8; 4096];
    let mut length = 0;
    // SAFETY: Node owns env/info and all argument/output buffers have the declared capacities.
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
        || count != 6
    {
        return Err(c"Invalid preview linkage");
    }
    // SAFETY: the first value is converted only into this bounded writable byte buffer.
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
    {
        return Err(c"Invalid preview module");
    }
    if filename[..length].contains(&0) {
        return Err(c"Invalid preview module");
    }
    let mut offsets = [0u32; 5];
    for (value, offset) in args[1..].iter().zip(&mut offsets) {
        // SAFETY: Node argument values are converted through the public uint32 API.
        if unsafe { (api.uint)(env, *value, offset) } != 0 || *offset == 0 {
            return Err(c"Invalid preview offset");
        }
    }
    // SAFETY: filename was NUL-terminated by Node; only an already loaded module is accepted.
    let raw = unsafe { libc::dlopen(filename.as_ptr().cast(), libc::RTLD_NOW | libc::RTLD_NOLOAD) };
    if raw.is_null() {
        return Err(c"Preview module is not loaded");
    }
    let module = Library(raw);
    let mut map: *mut ffi::link_map = ptr::null_mut();
    // SAFETY: dlinfo writes a link_map pointer owned by this retained dlopen reference.
    if unsafe { libc::dlinfo(raw, libc::RTLD_DI_LINKMAP, (&raw mut map).cast()) } != 0
        || map.is_null()
    {
        return Err(c"Preview module map unavailable");
    }
    // SAFETY: successful RTLD_DI_LINKMAP establishes a valid public ELF link_map.
    let base = unsafe { (*map).l_addr } as usize;
    if INSTALLED.load(Ordering::Acquire) != 0 {
        return if INSTALLED.load(Ordering::Acquire) == base {
            Ok(true)
        } else {
            Err(c"Preview module changed")
        };
    }
    let addresses = offsets.map(|offset| base + offset as usize);
    let [slot, original, buffer_vtable, handle, get_buffer] = addresses;
    for address in addresses {
        if permissions(address).is_none() {
            return Err(c"Preview address outside module mappings");
        }
    }
    if slot % std::mem::align_of::<AtomicUsize>() != 0 {
        return Err(c"Misaligned preview linkage");
    }
    // SAFETY: JS verified the whole native-module hash; scope generation reviewed the
    // exact vtable relocation, accessor bytes and VideoFrame buffer getter.
    let reviewed = unsafe {
        (slot as *const usize).read() == original
            && std::slice::from_raw_parts(handle as *const u8, 5) == [0x48, 0x8b, 0x47, 0x48, 0xc3]
            && std::slice::from_raw_parts(get_buffer as *const u8, 8)
                == [0x55, 0x48, 0x89, 0xe5, 0x53, 0x50, 0x48, 0x89]
    };
    if !reviewed {
        return Err(c"Preview ABI differs from review");
    }
    // SAFETY: the reviewed C++ callbacks/accessor use the x86-64 SysV pointer ABI.
    let hook = unsafe {
        preview::Hook {
            original: std::mem::transmute::<usize, unsafe extern "C" fn(*mut c_void, *const c_void)>(
                original,
            ),
            handle: std::mem::transmute::<usize, unsafe extern "C" fn(*const c_void) -> *mut c_void>(
                handle,
            ),
            buffer_vtable,
        }
    };
    // SAFETY: Linux sysconf has no pointer arguments or retained resources.
    let page = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if page <= 0 {
        return Err(c"Invalid page size");
    }
    let page = page as usize;
    let first = slot / page * page;
    let protection = permissions(slot).ok_or(c"Missing preview mapping")?;
    if !preview::install(hook) {
        return Err(c"Preview hook already configured");
    }
    // SAFETY: only this reviewed vtable page is temporarily writable; no executable body is changed.
    if unsafe { libc::mprotect(first as *mut c_void, page, protection | libc::PROT_WRITE) } != 0 {
        return Err(c"Cannot protect preview linkage");
    }
    // SAFETY: aligned native vtable pointer is published atomically after hook state initialization.
    unsafe {
        (*(slot as *const AtomicUsize))
            .store(preview::on_frame as *const () as usize, Ordering::Release);
    }
    // SAFETY: restore the original page permissions immediately after one atomic pointer write.
    if unsafe { libc::mprotect(first as *mut c_void, page, protection) } != 0 {
        // SAFETY: failure leaves the page writable, so the original pointer can be restored.
        unsafe {
            (*(slot as *const AtomicUsize)).store(original, Ordering::Release);
            libc::mprotect(first as *mut c_void, page, protection);
        }
        return Err(c"Cannot restore preview linkage protection");
    }
    INSTALLED.store(base, Ordering::Release);
    std::mem::forget(module);
    Ok(true)
}
unsafe extern "C" fn callback(env: Env, info: Info) -> Value {
    // SAFETY: Node invokes this callback with live Node-API arguments.
    match std::panic::catch_unwind(|| unsafe { install(env, info) }) {
        Ok(Ok(flag)) => {
            let mut out = ptr::null_mut();
            if let Some(api) = API.get() {
                // SAFETY: this callback's env remains valid for the boolean output.
                unsafe {
                    (api.boolean)(env, flag, &mut out);
                }
            }
            out
        }
        Ok(Err(message)) => failure(env, message),
        Err(_) => failure(env, c"Preview installation panicked"),
    }
}
unsafe extern "C" fn cleanup(environment: *mut c_void) {
    if let Some(envs) = ENVS.get() {
        let mut envs = envs
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        envs.remove(&(environment as usize));
        if envs.is_empty() {
            drop(envs);
            preview::shutdown();
        }
    }
}
/// # Safety
/// Node provides a live environment and exports object on its module-init thread.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_rust_preview_register(env: Env, exports: Value) -> Value {
    if API.get().is_none() {
        let Some(api) = Api::load() else {
            return ptr::null_mut();
        };
        let _ = API.set(api);
    }
    let Some(api) = API.get() else {
        return ptr::null_mut();
    };
    let mut function = ptr::null_mut();
    // SAFETY: Node owns env/exports; registration retains the static Rust callback only.
    if unsafe {
        (api.function)(
            env,
            c"installCapturePreview".as_ptr(),
            usize::MAX,
            Some(callback),
            ptr::null_mut(),
            &mut function,
        )
    } != 0
    {
        return ptr::null_mut();
    }
    // SAFETY: function is the value just created in this same Node environment.
    if unsafe { (api.property)(env, exports, c"installCapturePreview".as_ptr(), function) } != 0 {
        return ptr::null_mut();
    }
    let mut envs = ENVS
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if envs.insert(env as usize) {
        // SAFETY: each environment registers this callback/data pair exactly once,
        // even when several DPE adapters independently load this same addon.
        if unsafe { (api.cleanup)(env, Some(cleanup), env.cast()) } != 0 {
            envs.remove(&(env as usize));
            return ptr::null_mut();
        }
    }
    exports
}
