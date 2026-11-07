// SPDX-License-Identifier: MIT
//! A private hardware encoder, isolated from Electron's FFmpeg namespace.
#[cfg_attr(not(feature = "ffmpeg"), allow(dead_code))]
mod policy;
#[cfg(feature = "ffmpeg")]
mod runtime;
#[cfg(feature = "ffmpeg")]
mod ffi {
    #![allow(
        non_upper_case_globals,
        non_camel_case_types,
        non_snake_case,
        dead_code,
        unused_imports,
        clippy::all,
        clippy::undocumented_unsafe_blocks,
        unsafe_op_in_unsafe_fn
    )]
    include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
}
