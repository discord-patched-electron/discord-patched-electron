// SPDX-License-Identifier: MIT
//! Reviewed video call-site installation and the existing Node API. Other
//! PipeWire callers keep their original GOT entries and are never intercepted.
use crate::{
    call_patch::{self, Branch, Module},
    node::{self, Env, Info, Value},
};
use dpe_pipewire::{rates::Mode, upstream};
use std::{
    ffi::{CStr, CString, c_void},
    ptr,
    sync::{Mutex, OnceLock},
};

static INSTALL: Mutex<()> = Mutex::new(());
static PUBLISHED: OnceLock<([usize; 6], Mode)> = OnceLock::new();
static INSTALLED: OnceLock<()> = OnceLock::new();

unsafe fn arguments(env: Env, info: Info) -> Result<(CString, [u32; 6], Mode), &'static CStr> {
    let api = node::api().ok_or(c"Node API unavailable.")?;
    let mut args = [ptr::null_mut(); 8];
    let mut count = args.len();
    let mut filename = [0u8; 4096];
    let mut length = 0;
    // SAFETY: live Node callback and bounded argument/string outputs.
    if unsafe { (api.info)(env, info, &mut count, args.as_mut_ptr(), ptr::null_mut(), ptr::null_mut()) } != 0
        || count != args.len()
        // SAFETY: the first value belongs to this callback; output is bounded.
        || unsafe { (api.string)(env, args[0], filename.as_mut_ptr().cast(), filename.len(), &mut length) } != 0
        || length == 0 || length >= filename.len() - 1
    {
        return Err(c"Invalid upstream capture arguments.");
    }
    let filename =
        CString::new(&filename[..length]).map_err(|_| c"Invalid capture module filename.")?;
    let mut offsets = [0u32; 6];
    for (&arg, offset) in args[1..7].iter().zip(&mut offsets) {
        // SAFETY: scalar output and a value from this live callback.
        if unsafe { (api.uint)(env, arg, offset) } != 0 || *offset == 0 {
            return Err(c"Invalid upstream capture offset.");
        }
    }
    let mut mode = 2;
    // SAFETY: same scalar callback contract; zero selects initial mode.
    if unsafe { (api.uint)(env, args[7], &mut mode) } != 0 || mode > 1 {
        return Err(c"Invalid upstream capture mode.");
    }
    Ok((
        filename,
        offsets,
        if mode == 0 {
            Mode::Initial
        } else {
            Mode::Runtime
        },
    ))
}

fn linkage(module: &Module, slot: usize, target: usize, call: usize) -> bool {
    let Some(stub) = module.bytes(target, 14) else {
        return false;
    };
    let Some(code) = module.bytes(call, 6) else {
        return false;
    };
    let displacement = i32::from_le_bytes(code[2..6].try_into().expect("fixed instruction"));
    let pointer = i32::from_le_bytes(stub[7..11].try_into().expect("fixed stub"));
    if code[..2] != [0xff, 0x15]
        || call
            .checked_add(6)
            .and_then(|pc| pc.checked_add_signed(displacement as isize))
            != Some(slot)
        || stub[..7] != [0x55, 0x48, 0x89, 0xe5, 0x48, 0x8b, 0x05]
        || stub[11..] != [0x5d, 0xff, 0xe0]
        || !slot.is_multiple_of(align_of::<usize>())
        || !module
            .protection(slot, size_of::<usize>())
            .is_some_and(|p| p & libc::PROT_READ != 0 && p & libc::PROT_EXEC == 0)
        || !node::permissions_range(slot, size_of::<usize>())
            .is_some_and(|p| p & libc::PROT_READ != 0 && p & libc::PROT_EXEC == 0)
    {
        return false;
    }
    let Some(inner) = target
        .checked_add(11)
        .and_then(|pc| pc.checked_add_signed(pointer as isize))
    else {
        return false;
    };
    if !module
        .protection(inner, size_of::<usize>())
        .is_some_and(|p| p & libc::PROT_WRITE != 0)
        || !node::permissions_range(inner, size_of::<usize>())
            .is_some_and(|p| p & libc::PROT_WRITE != 0)
    {
        return false;
    }
    // SAFETY: retained module and full readable nonexecutable aligned word
    // validated above; startup installation is serialized before native use.
    unsafe { (slot as *const usize).read() == target }
}

unsafe fn install_inner(env: Env, info: Info) -> Result<(), &'static CStr> {
    // SAFETY: bounded live callback values are consumed synchronously.
    let (filename, offsets, mode) = unsafe { arguments(env, info) }?;
    let _lock = INSTALL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let module = Module::loaded(&filename)?;
    let mut addresses = [0usize; 6];
    for (address, offset) in addresses.iter_mut().zip(offsets) {
        *address = module.address(offset)?;
    }
    if let Some(previous) = PUBLISHED.get() {
        if previous != &(addresses, mode) {
            return Err(c"Upstream capture module changed.");
        }
        if INSTALLED.get().is_some() {
            return Ok(());
        }
        return Err(c"Previous upstream capture installation did not complete.");
    }
    if !linkage(&module, addresses[0], addresses[1], addresses[2])
        || !linkage(&module, addresses[3], addresses[4], addresses[5])
    {
        return Err(c"Upstream capture linkage differs from reviewed native code.");
    }
    // SAFETY: fixed system soname, retained for every installed native callback.
    let raw = unsafe {
        libc::dlopen(
            c"libpipewire-0.3.so.0".as_ptr(),
            libc::RTLD_NOW | libc::RTLD_LOCAL,
        )
    };
    if raw.is_null() {
        return Err(c"PipeWire stream listeners are unavailable.");
    }
    let library = node::Library(raw);
    macro_rules! symbol {
        ($name:literal, $type:ty) => {{
            // SAFETY: exact public PipeWire symbol and function signature.
            let pointer = unsafe { libc::dlsym(library.0, $name.as_ptr()) };
            if pointer.is_null() {
                None
            } else {
                // SAFETY: the named exported PipeWire function has this pinned ABI.
                Some(unsafe { std::mem::transmute::<*mut c_void, $type>(pointer) })
            }
        }};
    }
    use dpe_pipewire::ffi as f;
    let native = upstream::Native {
        // SAFETY: both complete reviewed stub bodies passed linkage above.
        connect: unsafe { std::mem::transmute::<usize, upstream::Connect>(addresses[1]) },
        // SAFETY: same validated update stub ABI.
        update: unsafe { std::mem::transmute::<usize, upstream::Update>(addresses[4]) },
        listen: symbol!(
            c"pw_stream_add_listener",
            unsafe extern "C" fn(
                *mut f::pw_stream,
                *mut f::spa_hook,
                *const f::pw_stream_events,
                *mut c_void,
            )
        )
        .ok_or(c"PipeWire stream listeners are unavailable.")?,
        core: symbol!(
            c"pw_stream_get_core",
            unsafe extern "C" fn(*mut f::pw_stream) -> *mut f::pw_core
        ),
        context: symbol!(
            c"pw_core_get_context",
            unsafe extern "C" fn(*mut f::pw_core) -> *mut f::pw_context
        ),
        loop_: symbol!(
            c"pw_context_get_main_loop",
            unsafe extern "C" fn(*mut f::pw_context) -> *mut f::pw_loop
        ),
        state: symbol!(
            c"pw_stream_get_state",
            unsafe extern "C" fn(*mut f::pw_stream, *mut *const libc::c_char) -> i32
        ),
    };
    if mode == Mode::Runtime
        && (native.core.is_none()
            || native.context.is_none()
            || native.loop_.is_none()
            || native.state.is_none())
    {
        return Err(c"PipeWire stream listeners are unavailable.");
    }
    let branches = [
        Branch {
            site: addresses[2],
            target: addresses[1],
            opcode: 0xff,
            replacement: upstream::connect as *const () as usize,
        },
        Branch {
            site: addresses[5],
            target: addresses[4],
            opcode: 0xff,
            replacement: upstream::update as *const () as usize,
        },
    ];
    // SAFETY: serialized startup; both reviewed call sites are validated before
    // mutation. Publish typed originals and retain libraries before redirecting.
    unsafe {
        call_patch::redirect_group(module, &branches, move || {
            // SAFETY: validated linkage, serialized startup, process-lived owners.
            let _ = upstream::initialize(native, mode);
            let _ = PUBLISHED.set((addresses, mode));
            std::mem::forget(library);
        })
    }?;
    let _ = INSTALLED.set(());
    Ok(())
}
fn undefined(env: Env) -> Value {
    let mut result = ptr::null_mut();
    if let Some(api) = node::api() {
        // SAFETY: live Node callback environment and writable result.
        unsafe {
            (api.undefined)(env, &mut result);
        }
    }
    result
}
unsafe extern "C" fn install(env: Env, info: Info) -> Value {
    // SAFETY: Node supplied this synchronous callback environment and arguments.
    match unsafe { install_inner(env, info) } {
        Ok(()) => undefined(env),
        Err(message) => node::failure(env, message),
    }
}
unsafe extern "C" fn rate(env: Env, info: Info) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    let mut count = 1;
    let mut value = ptr::null_mut();
    let mut fps = f64::NAN;
    // SAFETY: one callback argument and writable scalar outputs.
    let valid = unsafe {
        (api.info)(
            env,
            info,
            &mut count,
            &mut value,
            ptr::null_mut(),
            ptr::null_mut(),
        ) == 0
            && count == 1
            && (api.double)(env, value, &mut fps) == 0
    };
    if !valid || !fps.is_finite() || !(0.0..=240.0).contains(&fps) || fps.fract() != 0.0 {
        return node::failure(
            env,
            c"Upstream capture rate must be an integer from 0 through 240.",
        );
    }
    upstream::request(fps as u32);
    undefined(env)
}
unsafe extern "C" fn stats(env: Env, _: Info) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    let s = upstream::snapshot();
    let t = s.totals;
    let maximum = s.maximum.unwrap_or(dpe_pipewire::pod::Fraction {
        numerator: 0,
        denominator: 0,
    });
    let json = format!(
        concat!(
            "{{\"installed\":{},\"mode\":\"{}\",\"requestedFps\":{},\"activeStreams\":{},",
            "\"observedFormats\":{},\"supportedFormats\":{},\"rateChanges\":{},\"restorations\":{},",
            "\"errors\":{},\"bypassed\":{},\"pendingFps\":{},\"ackTimeouts\":{},\"asyncErrors\":{},",
            "\"appliedFps\":{},\"negotiatedMaxFpsNumerator\":{},\"negotiatedMaxFpsDenominator\":{},",
            "\"preferredFps\":{},\"initialPreferences\":{},\"initialWithoutRate\":{},\"lateRateChanges\":{},",
            "\"width\":{},\"height\":{}}}"
        ),
        INSTALLED.get().is_some(),
        if s.mode == Mode::Initial {
            "initial"
        } else {
            "runtime"
        },
        s.requested,
        s.active,
        t.observed,
        t.supported,
        t.changes,
        t.restored,
        t.failures,
        t.bypassed,
        s.pending,
        t.timeouts,
        t.asynchronous_errors,
        s.applied,
        maximum.numerator,
        maximum.denominator,
        s.preferred,
        t.initial_preferences,
        t.initial_without_rate,
        s.late_rates,
        s.width,
        s.height
    );
    let mut result = ptr::null_mut();
    // SAFETY: initialized UTF-8 buffer is borrowed only for this Node string copy.
    unsafe {
        (api.new_string)(env, json.as_ptr().cast(), json.len(), &mut result);
    }
    result
}
pub(crate) unsafe extern "C" fn register(env: Env, exports: Value) -> Value {
    // SAFETY: module init passes live Node values, and callbacks are static.
    unsafe {
        node::register_functions(
            env,
            exports,
            &[
                (c"installCaptureUpstreamV2", install),
                (c"setCaptureUpstreamRate", rate),
                (c"captureUpstreamStats", stats),
            ],
        )
    }
}
