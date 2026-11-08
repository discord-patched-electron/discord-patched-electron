// SPDX-License-Identifier: GPL-3.0-only
//! Small OS boundaries. Every successful acquisition immediately has an owner.
use crate::ffi;
use std::{
    ffi::{CStr, CString, c_void},
    io,
    mem::{self, MaybeUninit},
    os::{
        fd::{AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
        unix::{
            ffi::OsStrExt,
            fs::{FileTypeExt, MetadataExt, PermissionsExt},
        },
    },
    path::PathBuf,
    ptr,
    time::Duration,
};

/// Only the local consumer's transport calls use this classification. Hook,
/// DRM, protocol and fence errors keep their normal failure paths.
pub fn consumer_closed(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::BrokenPipe | io::ErrorKind::ConnectionReset | io::ErrorKind::NotConnected
    )
}

pub fn poll(fds: &mut [libc::pollfd], timeout: Duration) -> io::Result<()> {
    let timeout = libc::timespec {
        tv_sec: timeout.as_secs().try_into().unwrap_or(libc::time_t::MAX),
        tv_nsec: timeout.subsec_nanos().into(),
    };
    // SAFETY: initialized descriptor slice and a live bounded timeout. No signal-mask change.
    if unsafe { libc::ppoll(fds.as_mut_ptr(), fds.len() as _, &timeout, ptr::null()) } < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}
pub fn socket() -> io::Result<OwnedFd> {
    // SAFETY: creates an independent, initially unconnected local socket.
    let fd = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            0,
        )
    };
    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        // SAFETY: successful socket returned a unique live descriptor.
        Ok(unsafe { OwnedFd::from_raw_fd(fd) })
    }
}
pub struct Endpoint {
    pub server: Option<OwnedFd>,
    pub path: PathBuf,
    directory: PathBuf,
}
impl Endpoint {
    pub fn new() -> io::Result<Self> {
        let runtime = std::env::var_os("XDG_RUNTIME_DIR").ok_or(io::ErrorKind::NotFound)?;
        let runtime = PathBuf::from(runtime);
        Self::in_runtime(&runtime)
    }
    pub(crate) fn in_runtime(runtime: &std::path::Path) -> io::Result<Self> {
        if !runtime.is_absolute() {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        let mut template = CString::new(runtime.join("dpe-direct-XXXXXX").as_os_str().as_bytes())?
            .into_bytes_with_nul();
        // Leave room for the socket suffix before creating anything.
        if template.len() + 11 > 108 {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        // SAFETY: writable terminated template with exactly six trailing X bytes.
        if unsafe { libc::mkdtemp(template.as_mut_ptr().cast()) }.is_null() {
            return Err(io::Error::last_os_error());
        }
        let directory = PathBuf::from(std::ffi::OsStr::from_bytes(&template[..template.len() - 1]));
        let mut endpoint = Self {
            server: None,
            path: directory.join("frame.sock"),
            directory,
        };
        std::fs::set_permissions(&endpoint.directory, std::fs::Permissions::from_mode(0o700))?;
        let server = socket()?;
        // SAFETY: sockaddr_un is a plain C record and zero initialization leaves its path terminated.
        let mut address: libc::sockaddr_un = unsafe { mem::zeroed() };
        address.sun_family = libc::AF_UNIX as _;
        for (dst, src) in address
            .sun_path
            .iter_mut()
            .zip(endpoint.path.as_os_str().as_bytes())
        {
            *dst = *src as _;
        }
        // SAFETY: initialized sockaddr includes a bounded terminated pathname; socket is owned.
        if unsafe { libc::bind(server.as_raw_fd(), (&raw const address).cast(), size_of_val(&address) as _) } != 0
            // SAFETY: owned bound listening socket, backlog one matches the single-consumer contract.
            || unsafe { libc::listen(server.as_raw_fd(), 1) } != 0
        {
            return Err(io::Error::last_os_error());
        }
        endpoint.server = Some(server);
        Ok(endpoint)
    }
    pub fn accept(&self) -> io::Result<OwnedFd> {
        let server = self.server.as_ref().ok_or(io::ErrorKind::NotConnected)?;
        // SAFETY: owned listener; peer address is not requested, and flags apply atomically.
        let fd = unsafe {
            libc::accept4(
                server.as_raw_fd(),
                ptr::null_mut(),
                ptr::null_mut(),
                libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: successful accept creates a new uniquely owned descriptor.
        let fd = unsafe { OwnedFd::from_raw_fd(fd) };
        if !dpe_os::wire::dpe_direct_peer(fd.as_raw_fd()) {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
        Ok(fd)
    }
}
impl Drop for Endpoint {
    fn drop(&mut self) {
        drop(self.server.take());
        let _ = std::fs::remove_file(&self.path);
        let _ = std::fs::remove_dir(&self.directory);
    }
}
pub struct Descriptors<'a> {
    values: [BorrowedFd<'a>; 9],
    length: usize,
}
impl<'a> Descriptors<'a> {
    pub fn new(first: BorrowedFd<'a>) -> Self {
        Self {
            values: [first; 9],
            length: 0,
        }
    }
    pub fn push(&mut self, fd: BorrowedFd<'a>) -> io::Result<u32> {
        let index = self.length;
        let slot = self
            .values
            .get_mut(index)
            .ok_or(io::ErrorKind::InvalidData)?;
        *slot = fd;
        self.length += 1;
        Ok(index as u32)
    }
    pub fn as_slice(&self) -> &[BorrowedFd<'a>] {
        &self.values[..self.length]
    }
}
pub fn fences(descriptors: &[BorrowedFd<'_>]) -> io::Result<OwnedFd> {
    let mut merged: Option<OwnedFd> = None;
    for descriptor in descriptors {
        let mut export = ffi::dma_buf_export_sync_file {
            flags: ffi::DMA_BUF_SYNC_READ,
            fd: -1,
        };
        let request = libc::_IOWR::<ffi::dma_buf_export_sync_file>(b'b' as u32, 2);
        // SAFETY: documented Linux DMA-BUF ioctl, initialized output owned immediately on success.
        if unsafe { libc::ioctl(descriptor.as_raw_fd(), request, &mut export) } != 0 {
            return Err(io::Error::last_os_error());
        }
        if export.fd < 0 {
            return Err(io::ErrorKind::InvalidData.into());
        }
        // SAFETY: successful export ioctl transfers a new unique sync-file fd.
        let exported = unsafe { OwnedFd::from_raw_fd(export.fd) };
        merged = Some(if let Some(previous) = merged {
            let mut combine = ffi::sync_merge_data {
                fd2: exported.as_raw_fd(),
                fence: -1,
                ..Default::default()
            };
            for (dst, src) in combine.name.iter_mut().zip(b"DPE direct KMS") {
                *dst = *src as _;
            }
            let request = libc::_IOWR::<ffi::sync_merge_data>(b'>' as u32, 3);
            // SAFETY: both owned sync files remain live; successful merge initializes a new fence.
            if unsafe { libc::ioctl(previous.as_raw_fd(), request, &mut combine) } != 0 {
                return Err(io::Error::last_os_error());
            }
            if combine.fence < 0 {
                return Err(io::ErrorKind::InvalidData.into());
            }
            // SAFETY: successful merge transfers a unique new sync-file descriptor.
            unsafe { OwnedFd::from_raw_fd(combine.fence) }
        } else {
            exported
        });
    }
    merged.ok_or(io::ErrorKind::InvalidInput.into())
}
struct Library(*mut c_void);
impl Drop for Library {
    fn drop(&mut self) {
        // SAFETY: this object owns one successful dlopen reference, retained through every call below.
        unsafe {
            libc::dlclose(self.0);
        }
    }
}
impl Library {
    unsafe fn symbol<T: Copy>(&self, name: &CStr) -> io::Result<T> {
        // SAFETY: library pins the symbol; caller fixes the precise system ABI type T.
        let value = unsafe { libc::dlsym(self.0, name.as_ptr()) };
        if value.is_null() || size_of::<T>() != size_of_val(&value) {
            return Err(io::ErrorKind::Unsupported.into());
        }
        // SAFETY: caller establishes function-pointer ABI and pointer size was checked.
        Ok(unsafe { mem::transmute_copy(&value) })
    }
}
struct Instance {
    instance: ffi::VkInstance,
    destroy: unsafe extern "C" fn(ffi::VkInstance, *const ffi::VkAllocationCallbacks),
    _library: Library,
}
impl Drop for Instance {
    fn drop(&mut self) {
        // SAFETY: owned Vulkan instance, no children or asynchronous GPU work in inventory.
        unsafe {
            (self.destroy)(self.instance, ptr::null());
        }
    }
}
/// Vulkan is loaded only for game UUID inventory. KMS never maps the Vulkan loader.
pub fn render_device(uuid: [u8; 16], requested: &CStr) -> io::Result<(u32, u32)> {
    // SAFETY: static system soname, local immediate binding, uniquely owned loader reference.
    let library = Library(unsafe {
        libc::dlopen(
            c"libvulkan.so.1".as_ptr(),
            libc::RTLD_NOW | libc::RTLD_LOCAL,
        )
    });
    if library.0.is_null() {
        mem::forget(library);
        return Err(io::ErrorKind::Unsupported.into());
    }
    // SAFETY: symbols use the exact PFN ABI generated from the installed Vulkan headers.
    let (create, destroy, enumerate, properties) = unsafe {
        (
            library
                .symbol::<ffi::PFN_vkCreateInstance>(c"vkCreateInstance")?
                .ok_or(io::ErrorKind::Unsupported)?,
            library
                .symbol::<ffi::PFN_vkDestroyInstance>(c"vkDestroyInstance")?
                .ok_or(io::ErrorKind::Unsupported)?,
            library
                .symbol::<ffi::PFN_vkEnumeratePhysicalDevices>(c"vkEnumeratePhysicalDevices")?
                .ok_or(io::ErrorKind::Unsupported)?,
            library
                .symbol::<ffi::PFN_vkGetPhysicalDeviceProperties2>(
                    c"vkGetPhysicalDeviceProperties2",
                )?
                .ok_or(io::ErrorKind::Unsupported)?,
        )
    };
    let app = ffi::VkApplicationInfo {
        sType: ffi::VkStructureType_VK_STRUCTURE_TYPE_APPLICATION_INFO,
        pApplicationName: c"DPE direct capture inventory".as_ptr(),
        apiVersion: (1 << 22) | (1 << 12),
        ..Default::default()
    };
    let info = ffi::VkInstanceCreateInfo {
        sType: ffi::VkStructureType_VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        pApplicationInfo: &app,
        ..Default::default()
    };
    let mut instance = MaybeUninit::uninit();
    // SAFETY: valid instance descriptor and output; no callbacks/extensions or layers requested.
    if unsafe { create(&info, ptr::null(), instance.as_mut_ptr()) } != ffi::VkResult_VK_SUCCESS {
        return Err(io::ErrorKind::Unsupported.into());
    }
    let instance = Instance {
        // SAFETY: successful create initialized the owned instance.
        instance: unsafe { instance.assume_init() },
        destroy,
        _library: library,
    };
    let mut count = 0;
    // SAFETY: first enumeration requests only the output count from this live instance.
    if unsafe { enumerate(instance.instance, &mut count, ptr::null_mut()) }
        != ffi::VkResult_VK_SUCCESS
        || count > 32
    {
        return Err(io::ErrorKind::Unsupported.into());
    }
    let mut devices = [ptr::null_mut(); 32];
    // SAFETY: capacity count <= 32, enumeration writes only that many device handles.
    if unsafe { enumerate(instance.instance, &mut count, devices.as_mut_ptr()) }
        != ffi::VkResult_VK_SUCCESS
        || count > 32
    {
        return Err(io::ErrorKind::Unsupported.into());
    }
    for device in &devices[..count as usize] {
        let mut drm = ffi::VkPhysicalDeviceDrmPropertiesEXT {
            sType: ffi::VkStructureType_VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRM_PROPERTIES_EXT,
            ..Default::default()
        };
        let mut id = ffi::VkPhysicalDeviceIDProperties {
            sType: ffi::VkStructureType_VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES,
            pNext: (&raw mut drm).cast(),
            ..Default::default()
        };
        let mut output = ffi::VkPhysicalDeviceProperties2 {
            sType: ffi::VkStructureType_VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
            pNext: (&raw mut id).cast(),
            ..Default::default()
        };
        // SAFETY: live enumerated physical device, correctly linked output extension records.
        unsafe {
            properties(*device, &mut output);
        }
        if id.deviceUUID != uuid || drm.hasRender == 0 {
            continue;
        }
        let (Ok(major), Ok(minor)) = (
            u32::try_from(drm.renderMajor),
            u32::try_from(drm.renderMinor),
        ) else {
            continue;
        };
        let path = PathBuf::from(format!("/dev/dri/renderD{minor}"));
        if requested != c"auto" && requested.to_bytes() != path.as_os_str().as_bytes() {
            continue;
        }
        let Ok(node) = std::fs::symlink_metadata(path) else {
            continue;
        };
        if node.file_type().is_char_device()
            && libc::major(node.rdev()) == major
            && libc::minor(node.rdev()) == minor
        {
            return Ok((major, minor));
        }
    }
    Err(io::ErrorKind::NotFound.into())
}

pub fn sealed_bitmap(pixels: &[u32]) -> io::Result<OwnedFd> {
    // SAFETY: creates a private empty, sealable anonymous file.
    let fd = unsafe {
        libc::memfd_create(
            c"dpe-cursor".as_ptr(),
            libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: successful memfd_create transferred a unique descriptor.
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    // SAFETY: initialized u32 pixels have no padding; the read-only byte view cannot outlive pixels.
    let bytes =
        unsafe { std::slice::from_raw_parts(pixels.as_ptr().cast::<u8>(), size_of_val(pixels)) };
    use std::io::Write;
    let mut file = std::fs::File::from(fd);
    file.write_all(bytes)?;
    // SAFETY: the file is exclusively owned and no writable mappings exist; all bitmap mutations are forbidden after publication.
    if unsafe {
        libc::fcntl(
            file.as_raw_fd(),
            libc::F_ADD_SEALS,
            libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL,
        )
    } < 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(file.into())
}

#[cfg(test)]
pub(crate) fn pair() -> (OwnedFd, OwnedFd) {
    let mut pair = [-1; 2];
    let result =
        // SAFETY: two-element output for a new local socketpair, descriptors atomically close-on-exec/nonblocking.
        unsafe {
            libc::socketpair(
                libc::AF_UNIX,
                libc::SOCK_SEQPACKET | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
                0,
                pair.as_mut_ptr(),
            )
        };
    assert_eq!(result, 0);
    // SAFETY: successful socketpair transferred exactly two distinct live descriptors.
    unsafe { (OwnedFd::from_raw_fd(pair[0]), OwnedFd::from_raw_fd(pair[1])) }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::os::fd::AsFd;
    #[test]
    fn endpoint_cleanup_and_immutable_bitmap_ownership() {
        let endpoint = Endpoint::in_runtime(&std::env::temp_dir()).unwrap();
        let path = endpoint.path.clone();
        let directory = endpoint.directory.clone();
        assert!(
            std::fs::symlink_metadata(&path)
                .unwrap()
                .file_type()
                .is_socket()
        );
        assert_eq!(
            std::fs::metadata(&directory).unwrap().permissions().mode() & 0o777,
            0o700
        );
        drop(endpoint);
        assert!(!path.exists());
        assert!(!directory.exists());
        assert!(Endpoint::in_runtime(std::path::Path::new("relative")).is_err());
        let bitmap = sealed_bitmap(&[0x11223344, 0x55667788]).unwrap();
        // SAFETY: queries/read on the owned anonymous file; stack outputs have the exact requested capacity.
        unsafe {
            assert_eq!(libc::lseek(bitmap.as_raw_fd(), 0, libc::SEEK_END), 8);
            assert_ne!(
                libc::fcntl(bitmap.as_raw_fd(), libc::F_GET_SEALS) & libc::F_SEAL_WRITE,
                0
            );
            assert_eq!(
                libc::pwrite(bitmap.as_raw_fd(), [0u8; 4].as_ptr().cast(), 4, 0),
                -1
            );
        }
        let mut descriptors = Descriptors::new(bitmap.as_fd());
        for i in 0..9 {
            assert_eq!(descriptors.push(bitmap.as_fd()).unwrap(), i);
        }
        assert!(descriptors.push(bitmap.as_fd()).is_err());
        assert_eq!(descriptors.as_slice().len(), 9);
    }
}
