// SPDX-License-Identifier: GPL-3.0-only
//! Read-only libdrm records own their C allocations. Array borrows cannot
//! outlive a record; all reported counts are bounded before making a slice.
use crate::ffi;
use std::{
    ffi::{CStr, c_char},
    mem::MaybeUninit,
    ops::Deref,
    os::fd::{AsRawFd, OwnedFd},
    ptr::NonNull,
};
/// A checked DRM descriptor, retained for every metadata operation.
pub struct Node(OwnedFd);
impl Node {
    pub fn new(fd: OwnedFd) -> Option<Self> {
        // SAFETY: libdrm classifies this live descriptor without adopting it.
        let kind = unsafe { ffi::drmGetNodeTypeFromFd(fd.as_raw_fd()) };
        if kind == ffi::DRM_NODE_PRIMARY as i32 || kind == ffi::DRM_NODE_RENDER as i32 {
            Some(Self(fd))
        } else {
            None
        }
    }
    pub fn try_clone(&self) -> std::io::Result<Self> {
        self.0.try_clone().map(Self)
    }
    pub fn atomic_metadata(&self) -> bool {
        // SAFETY: these caps expose metadata only; they do not commit or change
        // any display state, and this is a verified DRM descriptor.
        unsafe {
            ffi::drmSetClientCap(
                self.as_raw_fd(),
                ffi::DRM_CLIENT_CAP_UNIVERSAL_PLANES as u64,
                1,
            ) == 0
                && ffi::drmSetClientCap(self.as_raw_fd(), ffi::DRM_CLIENT_CAP_ATOMIC as u64, 1) == 0
        }
    }
}
impl AsRawFd for Node {
    fn as_raw_fd(&self) -> i32 {
        self.0.as_raw_fd()
    }
}
pub struct Record<T> {
    pointer: NonNull<T>,
    destroy: unsafe extern "C" fn(*mut T),
}
impl<T> Deref for Record<T> {
    type Target = T;
    fn deref(&self) -> &T {
        // SAFETY: constructors adopt one successful libdrm allocation, which
        // remains immutable and live until this owner's matching destructor.
        unsafe { self.pointer.as_ref() }
    }
}
impl<T> Drop for Record<T> {
    fn drop(&mut self) {
        // SAFETY: this record uniquely owns the matching library allocation.
        unsafe {
            (self.destroy)(self.pointer.as_ptr());
        }
    }
}
unsafe fn array<T, O>(
    _owner: &Record<O>,
    pointer: *const T,
    count: usize,
    maximum: usize,
) -> Option<&[T]> {
    if count == 0 {
        return Some(&[]);
    }
    if count > maximum || pointer.is_null() || !pointer.is_aligned() {
        return None;
    }
    // SAFETY: each caller passes an array belonging to the borrowed libdrm
    // record, with its associated count. The library guarantees its allocation.
    Some(unsafe { std::slice::from_raw_parts(pointer, count) })
}
macro_rules! allocation {
    ($name:ident,$type:ty,$get:ident,$free:ident $(,$arg:ident:$arg_type:ty)*) => {
        pub fn $name(fd:&Node $(,$arg:$arg_type)*) -> Option<Record<$type>> {
            // SAFETY: live borrowed DRM descriptor and scalar identifiers;
            // libdrm returns a newly allocated record or a null failure.
            let pointer=NonNull::new(unsafe {ffi::$get(fd.as_raw_fd() $(,$arg)*)})?;
            Some(Record {pointer,destroy:ffi::$free})
        }
    }
}
allocation!(
    resources,
    ffi::drmModeRes,
    drmModeGetResources,
    drmModeFreeResources
);
allocation!(
    planes,
    ffi::drmModePlaneRes,
    drmModeGetPlaneResources,
    drmModeFreePlaneResources
);
allocation!(crtc,ffi::drmModeCrtc,drmModeGetCrtc,drmModeFreeCrtc,id:u32);
allocation!(connector,ffi::drmModeConnector,drmModeGetConnectorCurrent,drmModeFreeConnector,id:u32);
allocation!(property,ffi::drmModePropertyRes,drmModeGetProperty,drmModeFreeProperty,id:u32);
allocation!(framebuffer_record,ffi::drmModeFB2,drmModeGetFB2,drmModeFreeFB2,id:u32);
allocation!(blob,ffi::drmModePropertyBlobRes,drmModeGetPropertyBlob,drmModeFreePropertyBlob,id:u32);

pub struct Framebuffer<'a> {
    record: Record<ffi::drmModeFB2>,
    node: &'a Node,
}
impl Deref for Framebuffer<'_> {
    type Target = ffi::drmModeFB2;
    fn deref(&self) -> &Self::Target {
        &self.record
    }
}
impl Drop for Framebuffer<'_> {
    fn drop(&mut self) {
        for (index, &handle) in self.handles.iter().enumerate() {
            if handle != 0 && !self.handles[..index].contains(&handle) {
                // SAFETY: GETFB2 returns newly owned GEM handles, possibly
                // repeated across planes. Close each distinct handle once.
                unsafe {
                    ffi::drmCloseBufferHandle(self.node.as_raw_fd(), handle);
                }
            }
        }
    }
}
pub fn framebuffer(node: &Node, id: u32) -> Option<Framebuffer<'_>> {
    Some(Framebuffer {
        record: framebuffer_record(node, id)?,
        node,
    })
}
impl Record<ffi::drmModePropertyBlobRes> {
    pub fn bytes(&self) -> Option<&[u8]> {
        // SAFETY: libdrm owns length initialized bytes at data. The bounded
        // slice borrows this record, which releases the allocation on drop.
        unsafe { array(self, self.data.cast(), self.length as usize, 65536) }
    }
}

impl Record<ffi::drmModeRes> {
    pub fn connectors(&self) -> Option<&[u32]> {
        if self.count_connectors < 0 {
            return None;
        }
        // SAFETY: connector array and count belong to this owned resource record.
        unsafe { array(self, self.connectors, self.count_connectors as usize, 256) }
    }
}
impl Record<ffi::drmModePlaneRes> {
    pub fn ids(&self) -> Option<&[u32]> {
        // SAFETY: plane array and count belong to this owned resource record.
        unsafe { array(self, self.planes, self.count_planes as usize, 64) }
    }
}
const PROPERTY_CAPACITY: usize = 256;
const OBJECT_PROPERTIES_IOCTL: libc::Ioctl =
    libc::_IOWR::<ffi::drm_mode_obj_get_properties>(b'd' as u32, 0xb9);
const _: () = assert!(size_of::<ffi::drm_mode_obj_get_properties>() == 32);
pub struct Properties {
    ids: [MaybeUninit<u32>; PROPERTY_CAPACITY],
    values: [MaybeUninit<u64>; PROPERTY_CAPACITY],
    count: usize,
}
impl Properties {
    /// On success with a count within capacity, the callback must initialize
    /// every returned element, matching DRM_IOCTL_MODE_OBJ_GETPROPERTIES.
    /// Overflow and failed output are never read.
    unsafe fn query(
        id: u32,
        kind: u32,
        call: impl FnOnce(&mut ffi::drm_mode_obj_get_properties) -> i32,
    ) -> Option<Self> {
        let mut result = Self {
            ids: [MaybeUninit::uninit(); PROPERTY_CAPACITY],
            values: [MaybeUninit::uninit(); PROPERTY_CAPACITY],
            count: 0,
        };
        let mut storage = MaybeUninit::<ffi::drm_mode_obj_get_properties>::zeroed();
        // SAFETY: this integer-only Linux record admits all-zero initialization;
        // keeping it in place also initializes padding copied by the ioctl.
        let request = unsafe { storage.assume_init_mut() };
        request.props_ptr = result.ids.as_mut_ptr() as u64;
        request.prop_values_ptr = result.values.as_mut_ptr() as u64;
        request.count_props = PROPERTY_CAPACITY as u32;
        request.obj_id = id;
        request.obj_type = kind;
        if call(request) != 0 || request.count_props as usize > PROPERTY_CAPACITY {
            return None;
        }
        result.count = request.count_props as usize;
        Some(result)
    }
    pub fn pairs(&self) -> Option<impl ExactSizeIterator<Item = (u32, u64)> + '_> {
        // SAFETY: the successful kernel request initialized every element of
        // this bounded prefix. Uninitialized tail entries remain inaccessible.
        let (ids, values) = unsafe {
            (
                std::slice::from_raw_parts(self.ids.as_ptr().cast::<u32>(), self.count),
                std::slice::from_raw_parts(self.values.as_ptr().cast::<u64>(), self.count),
            )
        };
        Some(ids.iter().copied().zip(values.iter().copied()))
    }
    pub fn value(&self, id: u32) -> Option<u64> {
        self.pairs()?
            .find_map(|(key, value)| (key == id).then_some(value))
    }
}
pub fn object(fd: &Node, id: u32, kind: u32) -> Option<Properties> {
    // SAFETY: Node verifies a DRM file. The documented DRM ioctl writes both
    // complete arrays up to min(capacity, actual count) and returns that count.
    // The query rejects overflow or failure before making initialized slices.
    unsafe {
        Properties::query(id, kind, |request| {
            ffi::drmIoctl(
                fd.as_raw_fd(),
                OBJECT_PROPERTIES_IOCTL as libc::c_ulong,
                (request as *mut ffi::drm_mode_obj_get_properties).cast(),
            )
        })
    }
}
impl Record<ffi::drmModePropertyRes> {
    pub fn enums(&self) -> Option<&[ffi::drm_mode_property_enum]> {
        if self.count_enums < 0 {
            return None;
        }
        // SAFETY: enum array and count belong to this property record.
        unsafe { array(self, self.enums, self.count_enums as usize, 256) }
    }
}
pub fn named(name: &[c_char], expected: &[u8]) -> bool {
    name.iter()
        .map(|c| *c as u8)
        .take_while(|c| *c != 0)
        .eq(expected.iter().copied())
}
pub struct Devices {
    items: [ffi::drmDevicePtr; 16],
    count: usize,
}
pub struct Device<'a>(&'a ffi::drmDevice);
impl Devices {
    pub fn new() -> Option<Self> {
        let mut items = [std::ptr::null_mut(); 16];
        // SAFETY: exact output capacity passed to libdrm; successful entries
        // are owned by this list until drmFreeDevices releases them.
        let count = unsafe { ffi::drmGetDevices2(0, items.as_mut_ptr(), items.len() as i32) };
        let owner = Self {
            items,
            count: count.clamp(0, 16) as usize,
        };
        if !(0..=16).contains(&count) {
            return None;
        }
        Some(owner)
    }
    pub fn iter(&self) -> impl Iterator<Item = Device<'_>> {
        self.items[..self.count].iter().filter_map(|pointer| {
            // SAFETY: non-null entries are allocations owned by this list.
            unsafe { pointer.as_ref() }.map(Device)
        })
    }
}
impl Drop for Devices {
    fn drop(&mut self) {
        // SAFETY: this list owns at most the declared sixteen allocations.
        unsafe {
            ffi::drmFreeDevices(self.items.as_mut_ptr(), self.count as i32);
        }
    }
}
impl Device<'_> {
    pub fn node(&self, index: usize) -> Option<&CStr> {
        let device = self.0;
        if index > 2 || device.available_nodes & (1 << index) == 0 || device.nodes.is_null() {
            return None;
        }
        // SAFETY: libdrm device records contain DRM_NODE_MAX (three) path slots;
        // an available slot is a live NUL-terminated allocation owned by Devices.
        unsafe {
            let pointer = device.nodes.add(index).read();
            if pointer.is_null() {
                None
            } else {
                Some(CStr::from_ptr(pointer))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn property_snapshot_publishes_only_a_fully_initialized_bounded_prefix() {
        // SAFETY: the fake kernel initializes both elements before reporting
        // a successful count of two, including values above signed i64 range.
        let valid = unsafe {
            Properties::query(9, 7, |request| {
                assert_eq!(request.obj_id, 9);
                assert_eq!(request.obj_type, 7);
                assert_eq!(request.count_props, 256);
                let ids = request.props_ptr as *mut u32;
                let values = request.prop_values_ptr as *mut u64;
                ids.write(11);
                ids.add(1).write(19);
                values.write(0);
                values.add(1).write(u64::MAX);
                request.count_props = 2;
                0
            })
        }
        .unwrap();
        assert_eq!(
            valid.pairs().unwrap().collect::<Vec<_>>(),
            vec![(11, 0), (19, u64::MAX)]
        );
        // SAFETY: zero, overflow and failed replies do not publish or read any
        // element. These callbacks therefore need no initialized array prefix.
        unsafe {
            let empty = Properties::query(1, 2, |request| {
                request.count_props = 0;
                0
            })
            .unwrap();
            assert_eq!(empty.pairs().unwrap().len(), 0);
            assert!(
                Properties::query(1, 2, |request| {
                    request.count_props = 257;
                    0
                })
                .is_none()
            );
            assert!(
                Properties::query(1, 2, |request| {
                    request.count_props = 2;
                    -1
                })
                .is_none()
            );
        }
    }
}
