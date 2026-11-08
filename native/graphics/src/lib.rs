// SPDX-License-Identifier: GPL-3.0-only
//! Thread-confined graphics owners. Buffer storage retains its context even if
//! the C caller retires the device first. Opaque tokens never expose allocations.
mod context;
pub mod dmabuf;
#[cfg(feature = "game")]
mod game;
#[cfg(feature = "game")]
pub mod game_capture;
#[cfg(feature = "preview")]
pub mod game_preview;
#[cfg(feature = "game")]
mod game_render;
mod kms;
#[cfg(feature = "kms-capture")]
pub mod kms_capture;
mod target;
mod ffi {
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
