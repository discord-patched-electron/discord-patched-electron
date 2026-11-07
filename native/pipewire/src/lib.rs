// SPDX-License-Identifier: MIT
//! PipeWire fallback adapters. Optimized KMS/game transport uses the separate
//! direct producer, without starting a PipeWire loop or linking this crate.
pub mod pod;
#[cfg(feature = "source")]
pub mod publish;
pub mod rates;
#[cfg(feature = "relay")]
mod relay;
#[cfg(feature = "source")]
pub mod source;
#[cfg(feature = "source")]
pub mod source_dmabuf;
pub mod source_pods;
pub mod upstream;
#[allow(
    non_camel_case_types,
    non_snake_case,
    non_upper_case_globals,
    dead_code,
    clippy::all,
    clippy::undocumented_unsafe_blocks
)]
pub mod ffi {
    include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
}
