// SPDX-License-Identifier: MIT
pub mod import_cache;
pub mod profiler;
pub mod scheduling;
pub mod wire;

#[unsafe(no_mangle)]
pub extern "C" fn dpe_native_pointer_reserved(value: *const std::ffi::c_void) -> bool {
    dpe_core::handles::pointer_reserved(value as usize)
}
