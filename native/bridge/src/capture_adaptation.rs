// SPDX-License-Identifier: MIT
//! Preserve native scaling and synchronization with small, checked policy
//! adapters. Native objects and error allocations never become Rust-owned.
use crate::{
    call_patch::{self, Installed, Module},
    node::{self, Env, Info, Value},
};
use dpe_core::adaptation::{self, PixelLimit};
use std::{
    ffi::CStr,
    ptr,
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicU32, AtomicU64, AtomicUsize, Ordering},
    },
};

#[derive(Default)]
struct Hook {
    install: Mutex<()>,
    installed: OnceLock<Installed>,
    original: AtomicUsize,
}
static SCALE: Hook = Hook {
    install: Mutex::new(()),
    installed: OnceLock::new(),
    original: AtomicUsize::new(0),
};
static SYNC: Hook = Hook {
    install: Mutex::new(()),
    installed: OnceLock::new(),
    original: AtomicUsize::new(0),
};
static SCALE_COUNTS: [AtomicU64; 3] = [const { AtomicU64::new(0) }; 3];
static SCALE_SIZE: [AtomicU32; 3] = [const { AtomicU32::new(0) }; 3];
static SYNC_COUNTS: [AtomicU64; 4] = [const { AtomicU64::new(0) }; 4];
#[repr(C)]
#[derive(Clone, Copy)]
struct Resolution {
    width: usize,
    height: usize,
}
#[repr(C)]
#[derive(Clone, Copy)]
struct Wants {
    rotation: u8,
    black: u8,
    padding: [u8; 2],
    maximum: i32,
    target: i32,
    has_target: u8,
    target_padding: [u8; 3],
    fps: i32,
}
type Scale = unsafe extern "C" fn(usize, usize, u64) -> Resolution;
type Wait = unsafe extern "C" fn(i32, i32, u64, i64) -> usize;
const _: () = assert!(
    std::mem::offset_of!(Wants, maximum) == 4
        && std::mem::offset_of!(Wants, target) == 8
        && std::mem::offset_of!(Wants, has_target) == 12
);
const SCALE_CONTEXT: &[u8] = b"\x4c\x8d\x7c\x24\x28\x4c\x89\xff\x4c\x89\xf6\xe8\x10\x9d\xff\xff\x8b\xbb\xa0\x00\x00\x00\x8b\xb3\xa4\x00\x00\x00\x48\x89\xf8\x48\x09\xf0\x74\x34\x48\x8b\x54\x24\x30\xeb\x2f\x8b\xbb\xa0\x00\x00\x00\x8b\xb3\xa4\x00\x00\x00\x48\xba\x00\x00\x90\x00\x01\x00\x00\x00\xe8\x7a\x9f\xff\xff\x49\x89\xc4\x49\x89\xd6\x44\x89\x64\x24\x08\x44\x89\x74\x24\x0c\xeb\x32\x31\xd2";
const SYNC_CONTEXT: &[u8] =
    b"\x48\xb9\x00\xf2\x05\x2a\x01\x00\x00\x00\x89\xef\x44\x89\xf6\x4c\x89\xfa";
pub(crate) const SCALE_DISPATCH: &[u8] =
    &[0x48, 0x89, 0xd1, 0x4c, 0x89, 0xfa, 0xff, 0x25, 0, 0, 0, 0];
const SYNC_DISPATCH: &[u8] = &[0xff, 0x25, 0, 0, 0, 0];

unsafe extern "C" fn scaled(
    width: usize,
    height: usize,
    wants: *const Wants,
    native_target: u64,
) -> Resolution {
    SCALE_COUNTS[0].fetch_add(1, Ordering::Relaxed);
    let address = SCALE.original.load(Ordering::Acquire);
    if address == 0 {
        return Resolution { width, height };
    }
    // SAFETY: original is published only after validating the reviewed native
    // scaler target and exact SysV two-size_t return convention.
    let original = unsafe { std::mem::transmute::<usize, Scale>(address) };
    let limit = if wants.is_null() {
        PixelLimit::Invalid
    } else {
        // SAFETY: the scoped dispatch passes a live, constructed VideoSinkWants
        // prefix. Read initialized fields individually: C++ padding need not be
        // initialized and must never become a Rust byte array.
        unsafe {
            PixelLimit::from_wants(
                (*wants).rotation,
                (*wants).black,
                (*wants).maximum,
                (*wants).target,
                (*wants).has_target,
            )
        }
    };
    let PixelLimit::Pixels(limit) = limit else {
        if limit == PixelLimit::Invalid {
            SCALE_COUNTS[2].fetch_add(1, Ordering::Relaxed);
        }
        // SAFETY: unchanged native arguments and validated original ABI.
        return unsafe { original(width, height, native_target) };
    };
    // SAFETY: optional target is the reviewed packed presence/value scalar.
    let result = unsafe { original(width, height, (1u64 << 32) | u64::from(limit)) };
    let Some((width, height)) =
        adaptation::even_result((width, height), (result.width, result.height), limit)
    else {
        // SAFETY: preserve original behavior for an invalid adapted rectangle.
        return unsafe { original(width, height, native_target) };
    };
    SCALE_COUNTS[1].fetch_add(1, Ordering::Relaxed);
    for (value, count) in [limit, width as u32, height as u32]
        .into_iter()
        .zip(&SCALE_SIZE)
    {
        count.store(value, Ordering::Relaxed);
    }
    Resolution { width, height }
}
fn monotonic_deadline() -> Option<i64> {
    let mut now = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: fixed monotonic clock, initialized writable timespec.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut now) } != 0 {
        return None;
    }
    adaptation::wait_deadline(now.tv_sec, now.tv_nsec)
}
unsafe fn invoke_wait(
    original: Wait,
    drm: i32,
    sync: i32,
    point: u64,
    mut budget: i64,
    clock: impl FnOnce() -> Option<i64>,
) -> usize {
    SYNC_COUNTS[0].fetch_add(1, Ordering::Relaxed);
    if budget == adaptation::WAIT_BUDGET_NS {
        // SAFETY: errno is thread-local; the clock cannot change native errno.
        let saved = unsafe { *libc::__errno_location() };
        let deadline = clock();
        // SAFETY: restore only the calling thread's saved errno.
        unsafe {
            *libc::__errno_location() = saved;
        }
        if let Some(deadline) = deadline {
            budget = deadline;
            SYNC_COUNTS[1].fetch_add(1, Ordering::Relaxed);
        } else {
            SYNC_COUNTS[3].fetch_add(1, Ordering::Relaxed);
        }
    }
    // SAFETY: caller supplies the reviewed function; scalar arguments and opaque
    // anyhow return pass through unchanged. No error pointer is dereferenced.
    let result = unsafe { original(drm, sync, point, budget) };
    if result != 0 {
        SYNC_COUNTS[2].fetch_add(1, Ordering::Relaxed);
    }
    result
}
unsafe extern "C" fn wait_available(drm: i32, sync: i32, point: u64, budget: i64) -> usize {
    let address = SYNC.original.load(Ordering::Acquire);
    if address == 0 {
        return 0;
    }
    // SAFETY: original is published before the scoped call becomes reachable.
    unsafe {
        invoke_wait(
            std::mem::transmute::<usize, Wait>(address),
            drm,
            sync,
            point,
            budget,
            monotonic_deadline,
        )
    }
}
unsafe fn install<const IS_SCALE: bool>(env: Env, info: Info) -> Result<(), &'static CStr> {
    let hook = if IS_SCALE { &SCALE } else { &SYNC };
    let _guard = hook
        .install
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    // SAFETY: live Node callback arguments, checked bounded filename/offsets.
    let (filename, [call, target]) = unsafe { node::module_arguments::<2>(env, info) }?;
    let module = Module::loaded(&filename)?;
    let (site, target) = (module.address(call)?, module.address(target)?);
    if let Some(installed) = hook.installed.get() {
        return if installed.site == site && installed.target == target {
            Ok(())
        } else {
            Err(c"Native capture module changed.")
        };
    }
    let (context, dispatch, replacement) = if IS_SCALE {
        (SCALE_CONTEXT, SCALE_DISPATCH, scaled as *const () as usize)
    } else {
        (
            SYNC_CONTEXT,
            SYNC_DISPATCH,
            wait_available as *const () as usize,
        )
    };
    // SAFETY: JS whole-file guards and independent compiled context checks scope
    // this startup-only call. The install mutex serializes all state changes.
    let installed = unsafe {
        call_patch::redirect(module, site, target, context, dispatch, replacement, || {
            hook.original.store(target, Ordering::Release)
        })
    }?;
    let _ = hook.installed.set(installed);
    Ok(())
}
unsafe extern "C" fn install_callback<const IS_SCALE: bool>(env: Env, info: Info) -> Value {
    // SAFETY: Node invokes this callback with live environment and info.
    match std::panic::catch_unwind(|| unsafe { install::<IS_SCALE>(env, info) }) {
        Ok(Ok(())) => {
            let mut out = ptr::null_mut();
            if let Some(api) = node::api() {
                // SAFETY: live Node environment and writable result handle.
                unsafe {
                    (api.undefined)(env, &mut out);
                }
            }
            out
        }
        Ok(Err(error)) => node::failure(env, error),
        Err(_) => node::failure(env, c"Capture adapter installation failed."),
    }
}
unsafe extern "C" fn stats<const IS_SCALE: bool>(env: Env, _info: Info) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    let json = if IS_SCALE {
        let [calls, limited, invalid] = SCALE_COUNTS.each_ref().map(|x| x.load(Ordering::Relaxed));
        let [maximum, width, height] = SCALE_SIZE.each_ref().map(|x| x.load(Ordering::Relaxed));
        format!(
            "{{\"installed\":{},\"calls\":{calls},\"limited\":{limited},\"invalidWants\":{invalid},\"lastMaxPixels\":{maximum},\"lastWidth\":{width},\"lastHeight\":{height}}}",
            SCALE.installed.get().is_some()
        )
    } else {
        let [calls, corrected, errors, failures] =
            SYNC_COUNTS.each_ref().map(|x| x.load(Ordering::Relaxed));
        format!(
            "{{\"installed\":{},\"calls\":{calls},\"correctedWaits\":{corrected},\"waitErrors\":{errors},\"clockFailures\":{failures}}}",
            SYNC.installed.get().is_some()
        )
    };
    let mut out = ptr::null_mut();
    // SAFETY: Node copies these UTF-8 bytes before the String is dropped.
    unsafe {
        (api.new_string)(env, json.as_ptr().cast(), json.len(), &mut out);
    }
    out
}
pub(crate) unsafe extern "C" fn register_scale(env: Env, exports: Value) -> Value {
    // SAFETY: forwarded live Node init values and static Rust callbacks.
    unsafe {
        node::register_functions(
            env,
            exports,
            &[
                (c"installCaptureScale", install_callback::<true>),
                (c"captureScaleStats", stats::<true>),
            ],
        )
    }
}
pub(crate) unsafe extern "C" fn register_sync(env: Env, exports: Value) -> Value {
    // SAFETY: forwarded live Node init values and static Rust callbacks.
    unsafe {
        node::register_functions(
            env,
            exports,
            &[
                (c"installCaptureSync", install_callback::<false>),
                (c"captureSyncStats", stats::<false>),
            ],
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    unsafe extern "C" fn native_wait(drm: i32, sync: i32, point: u64, budget: i64) -> usize {
        assert_eq!((drm, sync, point), (19, 37, 0x123456789abcdef));
        // SAFETY: reading and replacing only this test thread's errno.
        unsafe {
            assert_eq!(*libc::__errno_location(), libc::EAGAIN);
            *libc::__errno_location() = libc::ENOSPC;
        }
        budget as usize
    }
    #[test]
    fn clock_errno_and_native_error_pointer_are_preserved() {
        for (input, clock, expected) in [
            (
                adaptation::WAIT_BUDGET_NS,
                Some(123461987654321),
                123461987654321,
            ),
            (adaptation::WAIT_BUDGET_NS, None, adaptation::WAIT_BUDGET_NS),
            (0, None, 0),
            (-1, None, -1),
            (i64::MAX, None, i64::MAX),
            (123, None, 123),
        ] {
            // SAFETY: test changes only its own thread-local errno.
            unsafe {
                *libc::__errno_location() = libc::EAGAIN;
            }
            // SAFETY: fixture callback uses exactly the reviewed scalar ABI.
            let result = unsafe {
                invoke_wait(native_wait, 19, 37, 0x123456789abcdef, input, || {
                    assert_eq!(input, adaptation::WAIT_BUDGET_NS);
                    // SAFETY: emulate a clock which changes this thread's errno.
                    *libc::__errno_location() = libc::EIO;
                    clock
                })
            };
            assert_eq!(result, expected as usize);
            // SAFETY: observes only the current test thread's errno.
            assert_eq!(unsafe { *libc::__errno_location() }, libc::ENOSPC);
        }
    }
}
