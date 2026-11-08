// SPDX-License-Identifier: MIT
#![cfg_attr(
    feature = "capture-delay-fixture",
    allow(
        dead_code,
        reason = "The disposable addon intentionally exposes only capture"
    )
)]
mod addon;
mod call_patch;
mod camera_flip;
mod camera_probe;
mod capture;
mod capture_abi;
mod capture_adaptation;
mod capture_delivery;
mod capture_picker;
mod capture_upstream;
#[cfg(feature = "capture-gpu")]
mod direct_bridge;
#[cfg(feature = "capture-gpu")]
mod direct_gpu;
mod encoder_adapter;
mod encoder_guard;
mod encoder_install;
mod embedded {
    include!(concat!(env!("OUT_DIR"), "/embedded.rs"));
}
mod gpu;
mod node;
mod picker_dispatch;
mod picker_install;
mod preview;
mod software_crop;
#[cfg(feature = "capture-gpu")]
mod va;
mod worker;
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

pub use dpe_os;

const _: () = assert!(std::mem::size_of::<ffi::dpe_discord_frame>() == 64);
const _: () = assert!(std::mem::offset_of!(ffi::dpe_discord_frame, width) == 48);
const _: () = assert!(std::mem::size_of::<ffi::encoder_nv12>() == 40);
