// SPDX-License-Identifier: GPL-3.0-only
//! Modifier-specific GBM storage. Plane descriptors borrow the allocation;
//! capture/encoder leases retain it independently of the allocator.
use crate::ffi as f;
use std::{
    ffi::{CStr, CString},
    os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
    rc::Rc,
};
pub struct Plane {
    descriptor: OwnedFd,
    pub stride: u32,
    pub offset: u32,
}
impl Plane {
    pub fn descriptor(&self) -> BorrowedFd<'_> {
        self.descriptor.as_fd()
    }
}
pub struct Allocation {
    context: Rc<Device>,
    handle: *mut f::gbm_bo,
    planes: Vec<Plane>,
    pub width: u32,
    pub height: u32,
    pub format: u32,
    pub modifier: u64,
}
struct Device {
    file: std::fs::File,
    handle: *mut f::gbm_device,
}
impl Drop for Device {
    fn drop(&mut self) {
        // SAFETY: this last device owner has no remaining GBM allocation children.
        unsafe {
            f::gbm_device_destroy(self.handle);
        }
    }
}
pub struct Allocator(Rc<Device>);
impl Allocator {
    pub fn open(node: &CStr) -> Option<Self> {
        use std::os::unix::{
            ffi::OsStrExt,
            fs::{FileTypeExt, OpenOptionsExt},
        };
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(std::path::Path::new(std::ffi::OsStr::from_bytes(
                node.to_bytes(),
            )))
            .ok()?;
        if !file.metadata().ok()?.file_type().is_char_device() {
            return None;
        }
        // SAFETY: file is an owned live device descriptor, retained by Device.
        let handle = unsafe { f::gbm_create_device(file.as_raw_fd()) };
        (!handle.is_null()).then(|| Self(Rc::new(Device { file, handle })))
    }
    pub fn device(&self) -> BorrowedFd<'_> {
        self.0.file.as_fd()
    }
    pub fn create(
        &self,
        width: u32,
        height: u32,
        format: u32,
        modifier: u64,
    ) -> Option<Allocation> {
        self.create_modifiers(width, height, format, &[modifier])
    }
    pub fn create_modifiers(
        &self,
        width: u32,
        height: u32,
        format: u32,
        modifiers: &[u64],
    ) -> Option<Allocation> {
        if !(1..=16384).contains(&width)
            || !(1..=16384).contains(&height)
            || !rgba(format)
            || modifiers.is_empty()
            || modifiers.len() > 256
            || modifiers.contains(&f::DPE_GRAPHICS_MOD_INVALID)
        {
            return None;
        }
        // SAFETY: retained GBM device, checked dimensions and one live modifier.
        let handle = unsafe {
            f::gbm_bo_create_with_modifiers2(
                self.0.handle,
                width,
                height,
                format,
                modifiers.as_ptr(),
                modifiers.len() as u32,
                f::DPE_GRAPHICS_RENDERING,
            )
        };
        if handle.is_null() {
            return None;
        }
        let mut result = Allocation {
            context: self.0.clone(),
            handle,
            planes: Vec::with_capacity(4),
            width,
            height,
            format,
            modifier: 0,
        };
        // SAFETY: result uniquely retains this complete GBM allocation.
        let (count, actual) = unsafe {
            (
                f::gbm_bo_get_plane_count(handle),
                f::gbm_bo_get_modifier(handle),
            )
        };
        if !(1..=4).contains(&count) || !modifiers.contains(&actual) {
            return None;
        }
        result.modifier = actual;
        for index in 0..count {
            // SAFETY: index is within the queried plane count; export transfers
            // one independent fd, adopted before any later validation.
            let (raw, stride, offset) = unsafe {
                (
                    f::gbm_bo_get_fd_for_plane(handle, index),
                    f::gbm_bo_get_stride_for_plane(handle, index),
                    f::gbm_bo_get_offset(handle, index),
                )
            };
            if raw < 0 {
                return None;
            }
            // SAFETY: successful GBM export returned one new descriptor.
            let descriptor = unsafe { OwnedFd::from_raw_fd(raw) };
            if stride == 0 || stride > 131072 || offset > i32::MAX as u32 {
                return None;
            }
            result.planes.push(Plane {
                descriptor,
                stride,
                offset,
            });
        }
        Some(result)
    }
}
impl Allocation {
    pub fn planes(&self) -> &[Plane] {
        &self.planes
    }
    pub fn device(&self) -> BorrowedFd<'_> {
        self.context.file.as_fd()
    }
}
impl Drop for Allocation {
    fn drop(&mut self) {
        // SAFETY: this sole owner destroys its BO while retaining the GBM context.
        unsafe {
            f::gbm_bo_destroy(self.handle);
        }
    }
}
pub fn rgba(format: u32) -> bool {
    matches!(
        format,
        f::DPE_GRAPHICS_ARGB | f::DPE_GRAPHICS_ABGR | f::DPE_GRAPHICS_XRGB | f::DPE_GRAPHICS_XBGR
    )
}
pub fn render_node(device: libc::dev_t) -> Option<CString> {
    use std::os::unix::{
        ffi::OsStrExt,
        fs::{FileTypeExt, MetadataExt},
    };
    // sysfs resolves DRM primary/render siblings by device identity. Never
    // compare the primary and render dev_t values as though they were equal.
    let selected = std::fs::canonicalize(format!(
        "/sys/dev/char/{}:{}/device",
        libc::major(device),
        libc::minor(device)
    ))
    .ok()?;
    for entry in std::fs::read_dir("/dev/dri").ok()?.flatten() {
        if !entry.file_name().as_bytes().starts_with(b"renderD") {
            continue;
        }
        let path = entry.path();
        let metadata = std::fs::symlink_metadata(&path).ok()?;
        if !metadata.file_type().is_char_device() {
            continue;
        }
        let dev = metadata.rdev();
        let identity = std::fs::canonicalize(format!(
            "/sys/dev/char/{}:{}/device",
            libc::major(dev),
            libc::minor(dev)
        ))
        .ok()?;
        if identity == selected {
            return CString::new(path.as_os_str().as_bytes()).ok();
        }
    }
    None
}
