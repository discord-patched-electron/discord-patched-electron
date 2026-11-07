// SPDX-License-Identifier: GPL-3.0-only
//! KMS protocol 6 compatibility, retaining GPU Screen Recorder's attribution.
//! The socket boundary adopts every descriptor before validating metadata.
pub mod capture;
mod drm;
mod export;
mod metadata;
mod permission;
mod scene;
pub mod server;
#[cfg(test)]
static FD_TESTS: std::sync::Mutex<()> = std::sync::Mutex::new(());
#[cfg(test)]
mod drm_fixture;
mod startup;
mod transport;
pub mod ffi {
    #![allow(
        non_upper_case_globals,
        non_camel_case_types,
        non_snake_case,
        dead_code,
        clippy::all,
        clippy::undocumented_unsafe_blocks,
        unsafe_op_in_unsafe_fn
    )]
    include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
}
const _: () = assert!(size_of::<ffi::gsr_kms_request>() == 12);
const _: () = assert!(size_of::<ffi::gsr_kms_response_item>() == 160);
const _: () = assert!(size_of::<ffi::gsr_kms_response>() == 1424);
const _: () = assert!(std::mem::offset_of!(ffi::gsr_kms_response, items) == 136);
const _: () = assert!(std::mem::offset_of!(ffi::gsr_kms_response, num_items) == 1416);
