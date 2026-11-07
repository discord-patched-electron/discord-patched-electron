// SPDX-License-Identifier: MIT
//! One Node entry point; Node symbols are resolved lazily so the same helper
//! can be LD_PRELOADed by non-Node processes without importing a Node runtime.
#[cfg(not(feature = "capture-delay-fixture"))]
use crate::{camera_flip, capture_adaptation, capture_upstream, picker_install};
use crate::{
    capture,
    node::{self, Env, Value},
};
use std::ptr;
#[cfg(not(feature = "capture-delay-fixture"))]
unsafe extern "C" {
    fn dpe_direct_capture_register(env: Env, exports: Value) -> Value;
}
/// # Safety
/// Node calls module initialization with its live environment and exports object.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn napi_register_module_v1(env: Env, exports: Value) -> Value {
    if node::api().is_none() {
        return ptr::null_mut();
    }
    #[cfg(feature = "capture-delay-fixture")]
    {
        // SAFETY: this disposable fixture receives the live module-init values.
        unsafe { capture::register_fixture(env, exports) }
    }
    #[cfg(not(feature = "capture-delay-fixture"))]
    {
        let register: [unsafe extern "C" fn(Env, Value) -> Value; 8] = [
            capture::register,
            capture_upstream::register,
            capture_adaptation::register_scale,
            capture_adaptation::register_sync,
            picker_install::register,
            dpe_direct_capture_register,
            node::dpe_rust_preview_register,
            crate::encoder_install::register,
        ];
        // SAFETY: this module-init callback owns the same live env and exports passed
        // unchanged to each adapter. None retain a JS value outside Node's lifetime.
        unsafe {
            if camera_flip::register(env, exports).is_null() {
                return ptr::null_mut();
            }
            for function in register {
                if function(env, exports).is_null() {
                    return ptr::null_mut();
                }
            }
        }
        exports
    }
}
