// SPDX-License-Identifier: MIT
//! Scope only Discord's reviewed legacy V4L2 probe; hotplug is never cached.
use crate::ffi;
use std::{
    ffi::{CString, c_void},
    sync::OnceLock,
};

// Stable Rust cannot define a C variadic function. This SysV tail trampoline
// preserves its optional third register argument and supplies the actual caller
// address to a fixed-signature Rust boundary. All decisions stay in Rust.
#[cfg(target_arch = "x86_64")]
std::arch::global_asm!(
    ".text",
    ".global ioctl",
    ".type ioctl,@function",
    "ioctl:",
    "mov rcx, [rsp]",
    "jmp dpe_camera_ioctl",
    ".size ioctl, .-ioctl"
);
type Ioctl = unsafe extern "C" fn(i32, libc::c_ulong, ...) -> i32;
struct Probe {
    next: Option<Ioctl>,
    module: Option<CString>,
    start: usize,
    end: usize,
}
static PROBE: OnceLock<Probe> = OnceLock::new();
fn address(name: &str) -> usize {
    let Ok(value) = std::env::var(name) else {
        return 0;
    };
    let text = value.trim_start();
    if text.starts_with('-') {
        return 0;
    }
    let text = text.strip_prefix('+').unwrap_or(text);
    if let Some(hex) = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
        usize::from_str_radix(hex, 16).unwrap_or(0)
    } else if text.starts_with('0') && text.len() > 1 {
        usize::from_str_radix(text, 8).unwrap_or(0)
    } else {
        text.parse().unwrap_or(0)
    }
}
impl Probe {
    fn initialize() -> Self {
        // SAFETY: RTLD_NEXT finds the next ioctl definition after this preload.
        let symbol = unsafe { libc::dlsym(libc::RTLD_NEXT, c"ioctl".as_ptr()) };
        let next = if symbol.is_null() {
            None
        } else {
            // SAFETY: x86-64 SysV ioctl consumes fd/request and optionally this
            // third register argument; passing it also preserves no-argument calls.
            Some(unsafe { std::mem::transmute::<*mut c_void, Ioctl>(symbol) })
        };
        let active = std::env::var_os("DPE_CAMERA_FAST_PROBE").is_none_or(|v| v != "0");
        Self {
            next,
            module: active
                .then(|| {
                    std::env::var_os("DPE_CAMERA_VOICE_MODULE")
                        .and_then(|s| CString::new(s.as_encoded_bytes()).ok())
                })
                .flatten(),
            start: address("DPE_CAMERA_PROBE_START"),
            end: address("DPE_CAMERA_PROBE_END"),
        }
    }
    fn scoped(&self, caller: *const c_void) -> bool {
        let Some(module) = &self.module else {
            return false;
        };
        if self.start == 0 || self.end <= self.start {
            return false;
        }
        // SAFETY: Dl_info is a C output record accepting an arbitrary code address.
        let mut info: libc::Dl_info = unsafe { std::mem::zeroed() };
        // SAFETY: dladdr only reads loader metadata for this borrowed return address.
        if unsafe { libc::dladdr(caller, &mut info) } == 0 || info.dli_fname.is_null() {
            return false;
        }
        // SAFETY: successful dladdr owns a terminated filename in retained loader metadata.
        if unsafe { std::ffi::CStr::from_ptr(info.dli_fname) } != module.as_c_str() {
            return false;
        }
        let Some(offset) = (caller as usize).checked_sub(info.dli_fbase as usize) else {
            return false;
        };
        (self.start..self.end).contains(&offset)
    }
    fn unsupported(&self, fd: i32, format: u32) -> bool {
        let Some(next) = self.next else {
            return false;
        };
        for index in 0..256 {
            let mut description = ffi::v4l2_fmtdesc {
                index,
                type_: 1,
                ..Default::default()
            };
            let request = libc::_IOWR::<ffi::v4l2_fmtdesc>(b'V' as u32, 2);
            // SAFETY: VIDIOC_ENUM_FMT has exactly this writable Linux UAPI record.
            let result = unsafe { next(fd, request, (&raw mut description).cast::<c_void>()) };
            if result < 0 {
                // SAFETY: errno is thread-local, valid on all supported Linux targets.
                return index > 0 && unsafe { *libc::__errno_location() } == libc::EINVAL;
            }
            if description.pixelformat == format {
                return false;
            }
        }
        false
    }
}
/// # Safety
/// Called by the ioctl trampoline with the original caller's request and optional
/// argument. For TRY_FMT the caller supplies a writable Linux v4l2_format record.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_camera_ioctl(
    fd: i32,
    request: libc::c_ulong,
    argument: *mut c_void,
    caller: *const c_void,
) -> i32 {
    // SAFETY: save the calling thread's errno across lazy initialization/probing.
    let previous = unsafe { *libc::__errno_location() };
    let probe = PROBE.get_or_init(Probe::initialize);
    let Some(next) = probe.next else {
        // SAFETY: errno belongs to this thread.
        unsafe {
            *libc::__errno_location() = libc::ENOSYS;
        }
        return -1;
    };
    if request == libc::_IOWR::<ffi::v4l2_format>(b'V' as u32, 64)
        && !argument.is_null()
        && probe.scoped(caller)
    {
        // SAFETY: only the kernel-defined TRY_FMT request establishes this argument type.
        let format = unsafe { &*argument.cast::<ffi::v4l2_format>() };
        // SAFETY: single-plane VIDEO_CAPTURE selects the pix member of this UAPI union.
        if format.type_ == 1 && probe.unsupported(fd, unsafe { format.fmt.pix.pixelformat }) {
            // SAFETY: explicit unsupported result follows the original ioctl errno contract.
            unsafe {
                *libc::__errno_location() = libc::EINVAL;
            }
            return -1;
        }
    }
    // SAFETY: restore errno before passing the original request/argument unchanged
    // to the next implementation. Its documented caller contract remains intact.
    unsafe {
        *libc::__errno_location() = previous;
        next(fd, request, argument)
    }
}
