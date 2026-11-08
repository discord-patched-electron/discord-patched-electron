// SPDX-License-Identifier: MIT
//! Node TSFN ownership is separate from active capture ownership. Cleanup
//! generations cannot alias a reused Node pointer after a document reload.
use crate::{
    capture_picker, ffi,
    node::{Env, Value},
};
use std::{
    ffi::{CStr, c_char, c_void},
    ptr,
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicUsize, Ordering},
    },
};
type Tsfn = ffi::napi_threadsafe_function;
#[repr(C)]
pub(crate) struct Api {
    string: unsafe extern "C" fn(Env, *const c_char, usize, *mut Value) -> i32,
    create: unsafe extern "C" fn(
        Env,
        Value,
        Value,
        Value,
        usize,
        usize,
        *mut c_void,
        ffi::napi_finalize,
        *mut c_void,
        ffi::napi_threadsafe_function_call_js,
        *mut Tsfn,
    ) -> i32,
    call: unsafe extern "C" fn(Tsfn, *mut c_void, u32) -> i32,
    release: unsafe extern "C" fn(Tsfn, u32) -> i32,
    unref: unsafe extern "C" fn(Env, Tsfn) -> i32,
    cleanup:
        unsafe extern "C" fn(Env, Option<unsafe extern "C" fn(*mut c_void)>, *mut c_void) -> i32,
    global: unsafe extern "C" fn(Env, *mut Value) -> i32,
    function: unsafe extern "C" fn(Env, Value, Value, usize, *const Value, *mut Value) -> i32,
}
static API: OnceLock<Api> = OnceLock::new();
struct Dispatcher {
    generation: usize,
    value: usize,
}
static CURRENT: Mutex<Option<Dispatcher>> = Mutex::new(None);
static NEXT: AtomicUsize = AtomicUsize::new(1);
struct Event {
    name: &'static str,
    id: u32,
    source: u32,
}
pub(super) fn available() -> bool {
    if API.get().is_some() {
        return true;
    }
    macro_rules! symbol {
        ($name:literal, $ty:ty) => {{
            // SAFETY: only exact public Node API names and signatures are used.
            let pointer = unsafe { libc::dlsym(libc::RTLD_DEFAULT, $name.as_ptr()) };
            if pointer.is_null() {
                return false;
            }
            // SAFETY: these signatures match the system Node API header.
            unsafe { std::mem::transmute::<*mut c_void, $ty>(pointer) }
        }};
    }
    let api = Api {
        string: symbol!(
            c"napi_create_string_utf8",
            unsafe extern "C" fn(Env, *const c_char, usize, *mut Value) -> i32
        ),
        create: symbol!(
            c"napi_create_threadsafe_function",
            unsafe extern "C" fn(
                Env,
                Value,
                Value,
                Value,
                usize,
                usize,
                *mut c_void,
                ffi::napi_finalize,
                *mut c_void,
                ffi::napi_threadsafe_function_call_js,
                *mut Tsfn,
            ) -> i32
        ),
        call: symbol!(
            c"napi_call_threadsafe_function",
            unsafe extern "C" fn(Tsfn, *mut c_void, u32) -> i32
        ),
        release: symbol!(
            c"napi_release_threadsafe_function",
            unsafe extern "C" fn(Tsfn, u32) -> i32
        ),
        unref: symbol!(
            c"napi_unref_threadsafe_function",
            unsafe extern "C" fn(Env, Tsfn) -> i32
        ),
        cleanup: symbol!(
            c"napi_add_env_cleanup_hook",
            unsafe extern "C" fn(
                Env,
                Option<unsafe extern "C" fn(*mut c_void)>,
                *mut c_void,
            ) -> i32
        ),
        global: symbol!(
            c"napi_get_global",
            unsafe extern "C" fn(Env, *mut Value) -> i32
        ),
        function: symbol!(
            c"napi_call_function",
            unsafe extern "C" fn(Env, Value, Value, usize, *const Value, *mut Value) -> i32
        ),
    };
    let _ = API.set(api);
    true
}
pub(super) fn active() -> bool {
    CURRENT
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .is_some()
}
pub(crate) unsafe extern "C" fn notify(name: *const c_char, id: u32, source: u32) -> bool {
    if name.is_null() {
        return false;
    }
    // SAFETY: the native registry supplies one of four process-lifetime names.
    let name = match unsafe { CStr::from_ptr(name) }.to_bytes() {
        b"request" => "request",
        b"cancelled" => "cancelled",
        b"started" => "started",
        b"stopped" => "stopped",
        _ => return false,
    };
    let Some(api) = API.get() else {
        return false;
    };
    let current = CURRENT
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let Some(current) = current.as_ref() else {
        return false;
    };
    let message = Box::into_raw(Box::new(Event { name, id, source }));
    // SAFETY: this lock retains the TSFN through its nonblocking enqueue. Node
    // never calls JS inline. Ownership transfers only when enqueue succeeds.
    if unsafe { (api.call)(current.value as Tsfn, message.cast(), 0) } == 0 {
        true
    } else {
        // SAFETY: unsuccessful enqueue did not take this unique Event allocation.
        drop(unsafe { Box::from_raw(message) });
        false
    }
}
unsafe extern "C" fn deliver(env: Env, callback: Value, _: *mut c_void, data: *mut c_void) {
    if data.is_null() {
        return;
    }
    // SAFETY: each successful enqueue transfers exactly one Event to this
    // callback, including Node's null-environment abort drain.
    let event = unsafe { Box::from_raw(data.cast::<Event>()) };
    if env.is_null() || callback.is_null() {
        return;
    }
    let Some(api) = API.get() else {
        return;
    };
    let json = format!(
        "{{\"event\":\"{}\",\"requestId\":{},\"sourceType\":{}}}",
        event.name, event.id, event.source
    );
    let mut value = ptr::null_mut();
    let mut global = ptr::null_mut();
    let mut ignored = ptr::null_mut();
    // SAFETY: Node invokes this on the live JS environment; the string is
    // copied before its buffer drops, and all output slots have exact sizes.
    unsafe {
        if (api.string)(env, json.as_ptr().cast(), json.len(), &mut value) == 0
            && (api.global)(env, &mut global) == 0
        {
            (api.function)(env, global, callback, 1, &value, &mut ignored);
        }
    }
}
unsafe extern "C" fn cleanup(expected: *mut c_void) {
    let mut retired = None;
    capture_picker::change_dispatch(|| {
        let mut current = CURRENT
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if !expected.is_null()
            && current
                .as_ref()
                .is_none_or(|current| current.generation != expected as usize)
        {
            return None;
        }
        retired = current.take();
        Some(false)
    });
    if let (Some(retired), Some(api)) = (retired, API.get()) {
        // SAFETY: this is the detached unique dispatcher owner. Abort drains
        // queued Event allocations through deliver with a null environment.
        unsafe {
            (api.release)(retired.value as Tsfn, 1);
        }
    }
}
pub(super) fn retire() {
    // SAFETY: null means detach the current generation, never dereference data.
    unsafe {
        cleanup(ptr::null_mut());
    }
}
/// # Safety
/// The live Node callback/environment are borrowed for this call; TSFN retains
/// the callback, not a raw napi_value, beyond it. Install calls are serialized.
pub(super) unsafe fn bind(env: Env, callback: Value) -> bool {
    if !available() {
        return false;
    }
    let api = API.get().expect("available Node API");
    let Ok(generation) = NEXT.try_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
        value.checked_add(1)
    }) else {
        return false;
    };
    let mut name = ptr::null_mut();
    let mut next = ptr::null_mut();
    // SAFETY: exact public Node API with live arguments, bounded queue and
    // process-lifetime delivery callback. No registry lock spans Node calls.
    if unsafe { (api.string)(env, c"DPE external capture".as_ptr(), usize::MAX, &mut name) } != 0
        // SAFETY: initialized resource name and live callback belong to env.
        || unsafe { (api.create)(env, callback, ptr::null_mut(), name, 64, 1, ptr::null_mut(), None, ptr::null_mut(), Some(deliver), &mut next) } != 0
    {
        return false;
    }
    // SAFETY: successful TSFN creation grants exactly one initial owner.
    if unsafe { (api.unref)(env, next) } != 0
        // SAFETY: only the monotonically allocated scalar token is retained.
        || unsafe { (api.cleanup)(env, Some(cleanup), generation as *mut c_void) } != 0
    {
        // SAFETY: creation succeeded but publication did not; abort that owner.
        unsafe {
            (api.release)(next, 1);
        }
        return false;
    }
    let mut previous = None;
    capture_picker::change_dispatch(|| {
        let mut current = CURRENT
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        previous = current.replace(Dispatcher {
            generation,
            value: next as usize,
        });
        Some(true)
    });
    if let Some(previous) = previous {
        // SAFETY: the previous unique owner was detached before releasing it.
        unsafe {
            (api.release)(previous.value as Tsfn, 1);
        }
    }
    true
}
#[cfg(feature = "picker-dispatch-fixture")]
mod fixture {
    use super::*;
    /// # Safety
    /// Disposable native fixture supplies the exact Node callback ABI table.
    #[unsafe(no_mangle)]
    pub(crate) unsafe extern "C" fn dpe_picker_dispatch_fixture_configure(api: Api) -> bool {
        API.set(api).is_ok()
    }
    /// # Safety
    /// Fixture borrows the mock environment/callback through binding.
    #[unsafe(no_mangle)]
    pub(crate) unsafe extern "C" fn dpe_picker_dispatch_fixture_bind(
        env: Env,
        callback: Value,
    ) -> bool {
        // SAFETY: disposable fixture promises the same borrow as bind.
        unsafe { bind(env, callback) }
    }
    #[unsafe(no_mangle)]
    pub extern "C" fn dpe_picker_dispatch_fixture_value() -> usize {
        CURRENT
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .as_ref()
            .map_or(0, |current| current.value)
    }
    #[unsafe(no_mangle)]
    pub extern "C" fn dpe_picker_dispatch_fixture_cleanup(expected: *mut c_void) {
        // SAFETY: expected is an opaque generation token, never dereferenced.
        unsafe {
            cleanup(expected);
        }
    }
    /// # Safety
    /// Name is a terminated known event name borrowed through notification.
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn dpe_picker_dispatch_fixture_notify(
        name: *const c_char,
        id: u32,
        source: u32,
    ) -> bool {
        // SAFETY: fixture supplies the same name borrow as the registry.
        unsafe { notify(name, id, source) }
    }
}
