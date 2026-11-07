// SPDX-License-Identifier: MIT
//! Dedicated typed Wayland connections. Cursor capture owns no PipeWire stream,
//! framework event loop, thread, full-window buffer or mapped foreign pixels.
pub mod capture;
mod cursor;
pub use cursor::{dpe_wl_cursor_create, dpe_wl_cursor_destroy, with_metadata};
