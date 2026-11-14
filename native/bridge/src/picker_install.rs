// SPDX-License-Identifier: MIT
//! Full-hash scope callers supply offsets; this boundary independently checks
//! module bounds, symbols, private storage, lazy linkage and call contexts.
use crate::{
    call_patch::{self, Branch, Module, Slot, Slots},
    capture_delivery as delivery, capture_picker as picker,
    node::{self, Env, Info, Value},
    picker_dispatch as dispatch,
};
use std::{
    ffi::{CStr, CString, c_void},
    ptr,
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};
static INSTALL: Mutex<()> = Mutex::new(());
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
static SHOW: AtomicUsize = AtomicUsize::new(0);
static WAKE: AtomicBool = AtomicBool::new(false);
static SCOPE: OnceLock<[u32; 14]> = OnceLock::new();
/// Linkage validation is also used by the reviewed direct-frame adapter. It
/// retains the loader while reading headers/slots, rather than trusting base.
#[unsafe(no_mangle)]
pub extern "C" fn dpe_capture_picker_linkage(base: usize, slot: usize, target: usize) -> i32 {
    let Ok(module) = Module::containing(target) else {
        return 0;
    };
    if module.base != base || !module.executable(target, 16) {
        return 0;
    }
    let candidate = Slot {
        address: slot,
        target,
        lazy: module.lazy_slot(slot),
        replacement: target,
    };
    if Slots::prepare(&module, &[candidate]).is_err() {
        return 0;
    }
    module.protection(slot, size_of::<usize>()).unwrap_or(0)
}
fn boolean(env: Env, value: bool) -> Value {
    let mut result = ptr::null_mut();
    if let Some(api) = node::api() {
        // SAFETY: live Node callback environment and writable result slot.
        unsafe {
            (api.boolean)(env, value, &mut result);
        }
    }
    result
}
fn undefined(env: Env) -> Value {
    let mut result = ptr::null_mut();
    if let Some(api) = node::api() {
        // SAFETY: live Node callback environment and writable result slot.
        unsafe {
            (api.undefined)(env, &mut result);
        }
    }
    result
}
unsafe fn values<const N: usize>(env: Env, info: Info) -> Result<[Value; N], &'static CStr> {
    let api = node::api().ok_or(c"Node API unavailable.")?;
    let mut values = [ptr::null_mut(); N];
    let mut count = N;
    // SAFETY: live Node invocation; buffer capacity equals the declared count.
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
        || count != N
    {
        return Err(c"Invalid capture picker arguments.");
    }
    Ok(values)
}
unsafe fn string<const N: usize>(
    env: Env,
    value: Value,
    empty: bool,
) -> Result<CString, &'static CStr> {
    let api = node::api().ok_or(c"Node API unavailable.")?;
    let mut bytes = [0u8; N];
    let mut length = 0;
    // SAFETY: bounded stack buffer receives only this live value's UTF-8 copy.
    if unsafe { (api.string)(env, value, bytes.as_mut_ptr().cast(), N, &mut length) } != 0
        || length >= N - 1
        || (!empty && length == 0)
    {
        return Err(c"Invalid capture picker string.");
    }
    CString::new(&bytes[..length]).map_err(|_| c"Invalid capture picker string.")
}
unsafe fn uint(env: Env, value: Value) -> Result<u32, &'static CStr> {
    let mut number = 0;
    let api = node::api().ok_or(c"Node API unavailable.")?;
    // SAFETY: live value is converted only through Node's public scalar API.
    if unsafe { (api.uint)(env, value, &mut number) } != 0 {
        return Err(c"Invalid capture picker number.");
    }
    Ok(number)
}
unsafe fn install(env: Env, info: Info) -> Result<Value, &'static CStr> {
    // SAFETY: caller passes this live Node invocation through unchanged.
    let args = unsafe { values::<16>(env, info)? };
    // SAFETY: only bounded copies/scalars are extracted from live arguments.
    let filename = unsafe { string::<4096>(env, args[0], false)? };
    let mut offsets = [0; 14];
    for (to, value) in offsets.iter_mut().zip(&args[1..15]) {
        // SAFETY: value belongs to the same live callback.
        *to = unsafe { uint(env, *value)? };
        if *to == 0 {
            return Err(c"Invalid capture picker offset.");
        }
    }
    let _guard = INSTALL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let module = Module::loaded(&filename)?;
    let slot = module.address(offsets[0])?;
    if INSTALLED.load(Ordering::Acquire) != 0 {
        if INSTALLED.load(Ordering::Acquire) != slot || SCOPE.get() != Some(&offsets) {
            return Err(c"Capture picker module changed.");
        }
        // SAFETY: same live environment/callback; serialized dispatcher rebind.
        return if dispatch::active() || unsafe { dispatch::bind(env, args[15]) } {
            Ok(undefined(env))
        } else {
            Err(c"Capture picker callback is unavailable.")
        };
    }
    let names = [
        c"cc_linux_show_picker",
        c"cc_linux_capture_new",
        c"cc_linux_capture_destroy",
        c"cc_linux_capture_update",
    ];
    let replacements = [
        picker::dpe_picker_show as *const () as usize,
        picker::dpe_picker_new as *const () as usize,
        picker::dpe_picker_destroy as *const () as usize,
        picker::dpe_picker_update as *const () as usize,
    ];
    let mut slots = Vec::with_capacity(4);
    let mut targets = [0; 4];
    for index in 0..4 {
        let at = if index == 3 { 9 } else { index * 2 };
        let address = module.address(offsets[at])?;
        let target = module.address(offsets[at + 1])?;
        if !module.executable(target, 16) || module.symbol(names[index]) != target {
            return Err(c"Capture picker linkage differs from reviewed native code.");
        }
        targets[index] = target;
        slots.push(Slot {
            address,
            target,
            lazy: module.lazy_slot(address),
            replacement: replacements[index],
        });
    }
    let transaction = Slots::prepare(&module, &slots)?;
    let selection = match offsets[1] {
        0xe5fe50 => 0x22ecb40u32,
        0xe2c3b0 => 0x22f1b40,
        0xe326f0 => 0x2311b40,
        0xe3ecf0 => 0x231fb90,
        0xe412b0 => 0x2323c10,
        _ => return Err(c"Unreviewed native selected storage."),
    };
    let selected = [selection, selection + 0x50, selection + 0x58];
    let sizes = [
        size_of::<libc::pthread_mutex_t>(),
        size_of::<crate::capture_abi::Configuration>(),
        size_of::<bool>(),
    ];
    let mut addresses = [0; 3];
    for index in 0..3 {
        addresses[index] = module.address(offsets[index + 6])?;
        if offsets[index + 6] != selected[index]
            || module.protection(addresses[index], sizes[index])
                != Some(libc::PROT_READ | libc::PROT_WRITE)
            || node::permissions_range(addresses[index], sizes[index])
                != Some(libc::PROT_READ | libc::PROT_WRITE)
        {
            return Err(c"Selected capture configuration differs from reviewed native code.");
        }
    }
    let mut frame = [0; 3];
    for (index, name) in [
        c"cc_video_frame_clone_i420",
        c"cc_i420_buffer_destroy",
        c"cc_video_frame_destroy",
    ]
    .into_iter()
    .enumerate()
    {
        frame[index] = module.address(offsets[11 + index])?;
        if !module.executable(frame[index], 16) || module.symbol(name) != frame[index] {
            return Err(c"Capture delivery linkage differs from reviewed native code.");
        }
    }
    // SAFETY: all addresses now match exact named exports and selected storage
    // in this retained module. Each conversion uses the reviewed C ABI.
    let native = unsafe {
        picker::Native {
            show: std::mem::transmute::<
                usize,
                unsafe extern "C" fn(*const crate::capture_abi::PickerOptions) -> i32,
            >(targets[0]),
            new: std::mem::transmute::<
                usize,
                unsafe extern "C" fn(
                    *const crate::capture_abi::CaptureOptions,
                    *mut *mut c_void,
                ) -> i32,
            >(targets[1]),
            destroy: std::mem::transmute::<usize, unsafe extern "C" fn(*mut c_void) -> i32>(
                targets[2],
            ),
            update: std::mem::transmute::<
                usize,
                unsafe extern "C" fn(*mut c_void, *const crate::capture_abi::CaptureOptions) -> i32,
            >(targets[3]),
            frames: delivery::Functions {
                clone: Some(std::mem::transmute::<
                    usize,
                    unsafe extern "C" fn(
                        *mut c_void,
                        *mut *const u8,
                        *mut usize,
                        *mut usize,
                        *mut usize,
                        *mut usize,
                        *mut usize,
                    ) -> *mut c_void,
                >(frame[0])),
                release_planar: Some(std::mem::transmute::<
                    usize,
                    unsafe extern "C" fn(*mut c_void),
                >(frame[1])),
                release_frame: Some(std::mem::transmute::<
                    usize,
                    unsafe extern "C" fn(*mut c_void),
                >(frame[2])),
            },
            selected_mutex: addresses[0],
            selected_config: addresses[1],
            selected_present: addresses[2],
            notify: dispatch::notify,
        }
    };
    // SAFETY: same live Node callback, serialized before native capture startup.
    if !unsafe { dispatch::bind(env, args[15]) } {
        return Err(c"Capture picker callback is unavailable.");
    }
    // SAFETY: checked linkage above; this is startup-only and retained below.
    if !unsafe { picker::dpe_picker_configure(native) } {
        dispatch::retire();
        return Err(c"Capture picker ownership is already configured.");
    }
    SHOW.store(targets[0], Ordering::Release);
    let _ = SCOPE.set(offsets);
    // Original targets stay callable even if page protection restoration fails.
    std::mem::forget(module);
    // SAFETY: every callback owner was published and the module is retained.
    if let Err(error) = unsafe { transaction.apply() } {
        dispatch::retire();
        return Err(error);
    }
    INSTALLED.store(slot, Ordering::Release);
    Ok(undefined(env))
}
const WAKE_OFFSETS: [[u32; 10]; 5] = [
    [
        0xeacb3d, 0xe96b20, 0xeb8086, 0xe96c30, 0xeb68d5, 0xe96c30, 0xe3dbee, 0xe46790, 0xe3dc16,
        0xe83ee0,
    ],
    [
        0xe8c30d, 0xe762f0, 0xe97856, 0xe76400, 0xe960a5, 0xe76400, 0xe3b14e, 0x14ecb50, 0xe3b176,
        0xe636b0,
    ],
    [
        0xe799cd, 0xe639b0, 0xe84f16, 0xe63ac0, 0xe83765, 0xe63ac0, 0xe2880e, 0x14da210, 0xe28836,
        0xe50d70,
    ],
    [
        0xe8ee2d, 0xe78e10, 0xe9a376, 0xe78f20, 0xe98bc5, 0xe78f20, 0xe5c83e, 0x14e76b0, 0xe5c866,
        0xe661d0,
    ],
    [
        0xe7fd0d, 0xe69cf0, 0xe8b256, 0xe69e00, 0xe89aa5, 0xe69e00, 0xe2eb4e, 0x14e0550, 0xe2eb76,
        0xe570b0,
    ],
];
unsafe fn install_wake(env: Env, info: Info) -> Result<bool, &'static CStr> {
    // SAFETY: live Node invocation is parsed through bounded public API calls.
    let (filename, offsets) = unsafe { node::module_arguments::<10>(env, info)? };
    let _guard = INSTALL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if INSTALLED.load(Ordering::Acquire) == 0 {
        return Ok(false);
    }
    let module = Module::loaded(&filename)?;
    let show = SHOW
        .load(Ordering::Acquire)
        .checked_sub(module.base)
        .ok_or(c"Capture wake module changed.")?;
    let expected = match show {
        0xe2c3b0 => WAKE_OFFSETS[2],
        0xe5fe50 => WAKE_OFFSETS[3],
        0xe326f0 => WAKE_OFFSETS[4],
        0xe3ecf0 => WAKE_OFFSETS[1],
        0xe412b0 => WAKE_OFFSETS[0],
        _ => return Ok(false),
    };
    if offsets != expected {
        return Ok(false);
    }
    if WAKE.load(Ordering::Acquire) {
        return Ok(true);
    }
    if !picker::dpe_picker_idle() {
        return Ok(false);
    }
    let before: [&[u8]; 5] = [
        &[0x48, 0x8b, 0x7f, 0x18, 0x48, 0x83, 0xc7, 0x10],
        &[0x48, 0x89, 0xdf, 0x48, 0x89, 0xc6],
        &[0x4c, 0x89, 0xff, 0x48, 0x89, 0xc6],
        &[
            0x89, 0xd1, 0x4c, 0x89, 0xe7, 0x4c, 0x89, 0xee, 0x48, 0x89, 0xc2,
        ],
        &[0x48, 0x89, 0xef],
    ];
    let after: [&[u8]; 5] = [
        &[0x48, 0x89, 0xc2, 0x31, 0xc0, 0x59, 0xc3],
        &[0x31, 0xed],
        &[0x48, 0x8d, 0xbc, 0x24, 0, 1, 0, 0],
        &[0x48, 0x8b, 0x84, 0x24, 8, 1, 0, 0],
        &[0x84, 0xc0, 0x74, 0xa0],
    ];
    let replacements = [
        delivery::dpe_delivery_read as *const () as usize,
        delivery::dpe_delivery_write as *const () as usize,
        delivery::dpe_delivery_write as *const () as usize,
        delivery::dpe_delivery_receive as *const () as usize,
        delivery::dpe_delivery_overdue as *const () as usize,
    ];
    let mut branches = Vec::with_capacity(5);
    for index in 0..5 {
        let site = module.address(offsets[index * 2])?;
        let target = module.address(offsets[index * 2 + 1])?;
        let size = if index == 4 { 6 } else { 5 };
        let context = site
            .checked_sub(before[index].len())
            .ok_or(c"Capture wake address overflow.")?;
        if module.bytes(context, before[index].len()) != Some(before[index])
            || module.bytes(site + size, after[index].len()) != Some(after[index])
            || !module.executable(target, 16)
        {
            return Ok(false);
        }
        branches.push(Branch {
            site,
            target,
            opcode: if index == 4 { 0xff } else { 0xe8 },
            replacement: replacements[index],
        });
    }
    // SAFETY: exact reviewed function contexts/targets above establish these
    // scalar/opaque-pointer C ABIs. Native Result storage remains native-owned.
    let configured = unsafe {
        delivery::dpe_delivery_configure_wake(
            Some(std::mem::transmute::<
                usize,
                unsafe extern "C" fn(*mut c_void) -> *mut c_void,
            >(branches[0].target)),
            Some(std::mem::transmute::<
                usize,
                unsafe extern "C" fn(*mut c_void, *mut c_void),
            >(branches[1].target)),
            Some(std::mem::transmute::<
                usize,
                unsafe extern "C" fn(
                    *mut c_void,
                    *mut c_void,
                    u64,
                    u32,
                ) -> std::mem::MaybeUninit<*mut c_void>,
            >(branches[3].target)),
            Some(std::mem::transmute::<
                usize,
                unsafe extern "C" fn(*mut c_void) -> bool,
            >(branches[4].target)),
        )
    };
    if !configured {
        return Ok(false);
    }
    // SAFETY: startup-only, every branch/context validated, originals published.
    unsafe {
        call_patch::redirect_group(module, &branches, || {})?;
    }
    if !delivery::dpe_delivery_enable_wake() {
        return Ok(false);
    }
    WAKE.store(true, Ordering::Release);
    Ok(true)
}
unsafe extern "C" fn install_js(env: Env, info: Info) -> Value {
    // SAFETY: Node owns this invocation; all native mutation is scoped above.
    match unsafe { install(env, info) } {
        Ok(result) => result,
        Err(error) => node::failure(env, error),
    }
}
unsafe extern "C" fn wake_js(env: Env, info: Info) -> Value {
    // SAFETY: Node owns this invocation; optional installation fails closed.
    boolean(env, unsafe { install_wake(env, info) }.unwrap_or(false))
}
unsafe extern "C" fn finish_js(env: Env, info: Info) -> Value {
    let run = || -> Result<bool, &'static CStr> {
        // SAFETY: live Node values are decoded through bounded public APIs.
        let args = unsafe { values::<4>(env, info)? };
        // SAFETY: all four values belong to this callback environment.
        let (id, action, path, node) = unsafe {
            (
                uint(env, args[0])?,
                string::<16>(env, args[1], false)?,
                string::<108>(env, args[2], true)?,
                uint(env, args[3])?,
            )
        };
        if id == 0
            || node == u32::MAX
            || ![c"selected", c"native", c"cancel", c"error"].contains(&action.as_c_str())
        {
            return Err(c"Invalid capture picker completion.");
        }
        // SAFETY: validated terminated owned strings live through completion.
        Ok(unsafe { picker::dpe_picker_finish(id, action.as_ptr(), path.as_ptr(), node) })
    };
    match run() {
        Ok(value) => boolean(env, value),
        Err(error) => node::failure(env, error),
    }
}
unsafe extern "C" fn cancel_js(env: Env, info: Info) -> Value {
    let run = || -> Result<bool, &'static CStr> {
        // SAFETY: live invocation is decoded through the public scalar API.
        let args = unsafe { values::<1>(env, info)? };
        // SAFETY: this value belongs to the current environment.
        let id = unsafe { uint(env, args[0])? };
        if id == 0 {
            return Err(c"Invalid capture picker cancellation.");
        }
        Ok(picker::dpe_picker_cancel(id))
    };
    match run() {
        Ok(value) => boolean(env, value),
        Err(error) => node::failure(env, error),
    }
}
pub(crate) unsafe extern "C" fn register(env: Env, exports: Value) -> Value {
    if !dispatch::available() {
        return exports;
    }
    // SAFETY: module init passes live values; registered callbacks are static.
    unsafe {
        node::register_functions(
            env,
            exports,
            &[
                (c"installCapturePicker", install_js),
                (c"installCaptureWake", wake_js),
                (c"finishCapturePicker", finish_js),
                (c"cancelCapturePicker", cancel_js),
            ],
        )
    }
}
