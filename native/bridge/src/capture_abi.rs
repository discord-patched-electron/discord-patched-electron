// SPDX-License-Identifier: MIT
//! Shared reviewed Linux C ABI. These are scalar/callback records, never a
//! construction or reinterpretation of the proprietary runtime's Rust objects.
use std::ffi::{c_char, c_void};
pub(crate) type FrameHandler = unsafe extern "C" fn(*mut c_void, u32, *const c_char, *mut c_void);
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct Configuration {
    pub fd: i32,
    pub node: u32,
}
pub(crate) type PickerCallback = unsafe extern "C" fn(*const PickerResult, *const c_void);
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct PickerOptions {
    pub source: u32,
    pub padding: u32,
    pub callback: Option<PickerCallback>,
    pub data: *const c_void,
}
#[repr(C)]
pub(crate) struct PickerResult {
    pub success: bool,
    pub padding: [u8; 3],
    pub config: Configuration,
    pub padding2: u32,
    pub error: *const c_char,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct CaptureOptions {
    pub source: u32,
    pub fps: u32,
    pub width: u32,
    pub height: u32,
    pub handler: Option<FrameHandler>,
    pub data: *mut c_void,
    pub config: *const Configuration,
}
const _: () = assert!(
    size_of::<Configuration>() == 8
        && size_of::<CaptureOptions>() == 40
        && std::mem::offset_of!(CaptureOptions, config) == 32
        && size_of::<PickerOptions>() == 24
        && std::mem::offset_of!(PickerOptions, callback) == 8
        && size_of::<PickerResult>() == 24
        && std::mem::offset_of!(PickerResult, config) == 4
        && std::mem::offset_of!(PickerResult, error) == 16
);
