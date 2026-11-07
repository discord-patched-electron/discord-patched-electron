// SPDX-License-Identifier: MIT
//! Disposable C-callback harness; none of these exports enter product builds.
use dpe_pipewire::{ffi as f, rates::Mode, upstream};
use std::ffi::c_char;

/// # Safety
/// The fixture supplies a complete immutable function table for process life.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn fixture_initialize(
    native: *const upstream::Native,
    runtime: bool,
) -> bool {
    // SAFETY: fixture owns this initialized table and all callbacks for process life.
    unsafe {
        upstream::initialize(
            *native,
            if runtime {
                Mode::Runtime
            } else {
                Mode::Initial
            },
        )
    }
}
/// # Safety
/// The fixture supplies the same serialized stream/POD contract as PipeWire.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn fixture_connect(
    stream: *mut f::pw_stream,
    direction: u32,
    target: u32,
    flags: u32,
    params: *const *const f::spa_pod,
    count: u32,
) -> i32 {
    // SAFETY: forwarded fixture-native stream call.
    unsafe { upstream::connect(stream, direction, target, flags, params, count) }
}
/// # Safety
/// The fixture retains this stream and all borrowed parameters during the call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn fixture_update(
    stream: *mut f::pw_stream,
    params: *const *const f::spa_pod,
    count: u32,
) -> i32 {
    // SAFETY: forwarded fixture-native stream call.
    unsafe { upstream::update(stream, params, count) }
}
#[unsafe(no_mangle)]
pub extern "C" fn fixture_request(fps: u32) {
    upstream::request(fps);
}
/// # Safety
/// The fixture supplies capacity writable bytes for this metadata-only result.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn fixture_snapshot(output: *mut c_char, capacity: usize) -> usize {
    let s = upstream::snapshot();
    let max = s.maximum.map_or(0, |f| f.numerator);
    let json = format!(
        "{{\"active\":{},\"preferred\":{},\"applied\":{},\"pending\":{},\"maximum\":{},\"width\":{},\"height\":{},\"changes\":{},\"restored\":{},\"errors\":{},\"bypassed\":{},\"asyncErrors\":{},\"initial\":{},\"withoutRate\":{},\"late\":{}}}",
        s.active,
        s.preferred,
        s.applied,
        s.pending,
        max,
        s.width,
        s.height,
        s.totals.changes,
        s.totals.restored,
        s.totals.failures,
        s.totals.bypassed,
        s.totals.asynchronous_errors,
        s.totals.initial_preferences,
        s.totals.initial_without_rate,
        s.late_rates
    );
    if json.len() >= capacity {
        return 0;
    }
    // SAFETY: caller grants capacity writable bytes; checked before copy/NUL.
    unsafe {
        std::ptr::copy_nonoverlapping(json.as_ptr(), output.cast(), json.len());
        output.add(json.len()).write(0);
    }
    json.len()
}
