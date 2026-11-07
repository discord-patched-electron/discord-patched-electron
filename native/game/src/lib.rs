// SPDX-License-Identifier: MIT
//! Opted-in game producer control. One owned socket, one current frame lease
//! and one retired release token bound storage and prevent reuse before ACK.
mod gl_sync;
mod hook;
pub mod protocol;
mod vulkan_format;
mod graphics_ffi {
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
    include!(concat!(env!("OUT_DIR"), "/graphics.rs"));
}
