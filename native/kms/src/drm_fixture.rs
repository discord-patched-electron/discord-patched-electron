// SPDX-License-Identifier: GPL-3.0-only
//! Test-only libdrm boundary. Strong test symbols replace system queries without
//! DRM access, root, captured content or mocks in any production artifact.
use crate::{drm, export::Exporter, ffi, transport};
use std::{
    os::fd::{AsFd, AsRawFd},
    sync::atomic::{AtomicBool, AtomicU32, Ordering},
};
static DESCRIPTIONS: AtomicU32 = AtomicU32::new(0);
static FRAMEBUFFERS: AtomicU32 = AtomicU32::new(0);
static HANDLES_CLOSED: AtomicU32 = AtomicU32::new(0);
static EXPORTS: AtomicU32 = AtomicU32::new(0);
static FAIL_SECOND_EXPORT: AtomicBool = AtomicBool::new(false);
static REMOVE_PROPERTY: AtomicBool = AtomicBool::new(false);
static CONNECTOR: AtomicU32 = AtomicU32::new(31);
static SECOND_CONNECTOR: AtomicBool = AtomicBool::new(false);
const PLANE: u32 = 41;
const NAMES: [&[u8]; 13] = [
    b"FB_ID",
    b"CRTC_ID",
    b"type",
    b"rotation",
    b"SRC_X",
    b"SRC_Y",
    b"SRC_W",
    b"SRC_H",
    b"CRTC_X",
    b"CRTC_Y",
    b"CRTC_W",
    b"CRTC_H",
    b"zpos",
];
#[unsafe(export_name = "drmGetNodeTypeFromFd")]
extern "C" fn kind(_: i32) -> i32 {
    ffi::DRM_NODE_PRIMARY as i32
}
#[unsafe(export_name = "drmModeGetResources")]
extern "C" fn resources(_: i32) -> *mut ffi::drmModeRes {
    let mut ids = vec![CONNECTOR.load(Ordering::Relaxed)];
    if SECOND_CONNECTOR.load(Ordering::Relaxed) {
        ids.push(33);
    }
    let count_connectors = ids.len() as i32;
    let connectors = Box::into_raw(ids.into_boxed_slice()).cast();
    Box::into_raw(Box::new(ffi::drmModeRes {
        count_connectors,
        connectors,
        ..Default::default()
    }))
}
#[unsafe(export_name = "drmModeFreeResources")]
unsafe extern "C" fn resources_free(record: *mut ffi::drmModeRes) {
    // SAFETY: the matching fake allocation owns exactly this record and array.
    unsafe {
        let owner = Box::from_raw(record);
        drop(Box::from_raw(std::ptr::slice_from_raw_parts_mut(
            owner.connectors,
            owner.count_connectors as usize,
        )));
    }
}
#[unsafe(export_name = "drmModeGetPlaneResources")]
extern "C" fn planes(_: i32) -> *mut ffi::drmModePlaneRes {
    let planes = Box::into_raw(Box::new([PLANE])).cast();
    Box::into_raw(Box::new(ffi::drmModePlaneRes {
        count_planes: 1,
        planes,
    }))
}
#[unsafe(export_name = "drmModeFreePlaneResources")]
unsafe extern "C" fn planes_free(record: *mut ffi::drmModePlaneRes) {
    // SAFETY: matching fake allocation owns the record and its one-element array.
    unsafe {
        let owner = Box::from_raw(record);
        drop(Box::from_raw(owner.planes.cast::<[u32; 1]>()));
    }
}
#[unsafe(export_name = "drmModeGetProperty")]
extern "C" fn property(_: i32, id: u32) -> *mut ffi::drmModePropertyRes {
    DESCRIPTIONS.fetch_add(1, Ordering::Relaxed);
    let name = match id {
        1 => b"CRTC_ID".as_slice(),
        2 => b"HDR_OUTPUT_METADATA".as_slice(),
        101..=113 => NAMES[(id - 101) as usize],
        _ => return std::ptr::null_mut(),
    };
    let mut record = ffi::drmModePropertyRes {
        prop_id: id,
        ..Default::default()
    };
    for (to, &from) in record.name.iter_mut().zip(name) {
        *to = from as libc::c_char;
    }
    Box::into_raw(Box::new(record))
}
#[unsafe(export_name = "drmModeFreeProperty")]
unsafe extern "C" fn property_free(record: *mut ffi::drmModePropertyRes) {
    // SAFETY: matching owned fake property, with no auxiliary allocations.
    unsafe {
        drop(Box::from_raw(record));
    }
}
#[unsafe(export_name = "drmIoctl")]
unsafe extern "C" fn ioctl(_: i32, request: libc::c_ulong, data: *mut libc::c_void) -> i32 {
    assert_eq!(
        request,
        libc::_IOWR::<ffi::drm_mode_obj_get_properties>(b'd' as u32, 0xb9) as libc::c_ulong
    );
    // SAFETY: the production caller supplies this exact initialized request.
    let request = unsafe { &mut *data.cast::<ffi::drm_mode_obj_get_properties>() };
    let (ids, values) = if request.obj_id == PLANE {
        let mut ids = (101..=113).collect::<Vec<_>>();
        let mut values = vec![
            77,
            9,
            u64::from(ffi::DRM_PLANE_TYPE_PRIMARY),
            1,
            0,
            0,
            1920 << 16,
            1080 << 16,
            0,
            0,
            1920,
            1080,
            0,
        ];
        if REMOVE_PROPERTY.load(Ordering::Relaxed) {
            ids.remove(1);
            values.remove(1);
        }
        (ids, values)
    } else {
        assert!(
            request.obj_id == CONNECTOR.load(Ordering::Relaxed)
                || (request.obj_id == 33 && SECOND_CONNECTOR.load(Ordering::Relaxed))
        );
        (
            vec![1, 2],
            vec![if request.obj_id == 33 { 10 } else { 9 }, 0],
        )
    };
    for index in 0..ids.len().min(request.count_props as usize) {
        // SAFETY: simulated ioctl honors the caller's declared array capacity,
        // initializing both values before reporting the complete actual count.
        unsafe {
            (request.props_ptr as *mut u32).add(index).write(ids[index]);
            (request.prop_values_ptr as *mut u64)
                .add(index)
                .write(values[index]);
        }
    }
    request.count_props = ids.len() as u32;
    0
}
#[unsafe(export_name = "drmModeGetFB2")]
extern "C" fn framebuffer(_: i32, id: u32) -> *mut ffi::drmModeFB2 {
    assert_eq!(id, 77);
    let sequence = FRAMEBUFFERS.fetch_add(1, Ordering::Relaxed);
    let handle = 1000 + sequence;
    Box::into_raw(Box::new(ffi::drmModeFB2 {
        fb_id: id,
        width: 1920,
        height: 1080,
        pixel_format: u32::from_le_bytes(*b"XR24"),
        handles: [handle, handle, 0, 0],
        pitches: [7680, 7680, 0, 0],
        ..Default::default()
    }))
}
#[unsafe(export_name = "drmModeFreeFB2")]
unsafe extern "C" fn framebuffer_free(record: *mut ffi::drmModeFB2) {
    // SAFETY: matching fake GETFB2 allocation; handle lifetime is checked below.
    unsafe {
        drop(Box::from_raw(record));
    }
}
#[unsafe(export_name = "drmCloseBufferHandle")]
extern "C" fn handle_close(_: i32, handle: u32) -> i32 {
    let index = HANDLES_CLOSED.fetch_add(1, Ordering::Relaxed);
    assert_eq!(handle, 1000 + index);
    0
}
#[unsafe(export_name = "drmPrimeHandleToFD")]
unsafe extern "C" fn export(_: i32, _: u32, flags: u32, output: *mut i32) -> i32 {
    assert_ne!(flags & libc::O_CLOEXEC as u32, 0);
    let call = EXPORTS.fetch_add(1, Ordering::Relaxed);
    if FAIL_SECOND_EXPORT.load(Ordering::Relaxed) && call % 2 == 1 {
        return -1;
    }
    // SAFETY: fake export creates a unique harmless fd and initializes exactly
    // the one declared output, matching PRIME's ownership contract.
    unsafe {
        let raw = libc::open(c"/dev/null".as_ptr(), libc::O_RDONLY | libc::O_CLOEXEC);
        assert!(raw >= 0);
        output.write(raw);
    }
    0
}
#[test]
fn exporter_caches_descriptions_but_refreshes_handles_and_releases_partial_exports() {
    use std::os::unix::net::UnixDatagram;
    let _exclusive = crate::FD_TESTS.lock().unwrap();
    let (left, right) = UnixDatagram::pair().unwrap();
    let node = drm::Node::new(left.as_fd().try_clone_to_owned().unwrap()).unwrap();
    let mut exporter = Exporter::new(node);
    let initial = std::fs::read_dir("/proc/self/fd").unwrap().count();
    let receive = |expected| {
        let mut response = ffi::gsr_kms_response::default();
        // SAFETY: retained socket and exclusive initialized output record.
        let result = unsafe { transport::dpe_kms_receive(right.as_raw_fd(), &mut response) };
        assert_eq!(result, expected);
        if result == 1 {
            assert_eq!(response.num_items, 1);
            assert_eq!(
                response.items[0].connector_id,
                CONNECTOR.load(Ordering::Relaxed)
            );
            assert_eq!(response.items[0].num_dma_bufs, 2);
        }
        // SAFETY: release exactly the descriptors transferred by successful receive.
        unsafe {
            transport::dpe_kms_response_close(&mut response);
        }
    };
    for _ in 0..3 {
        let packet = exporter.snapshot(None);
        assert!(packet.send(left.as_fd()));
        drop(packet);
        receive(1);
        assert_eq!(std::fs::read_dir("/proc/self/fd").unwrap().count(), initial);
    }
    assert_eq!(DESCRIPTIONS.load(Ordering::Relaxed), 15);
    assert_eq!(FRAMEBUFFERS.load(Ordering::Relaxed), 3);
    assert_eq!(HANDLES_CLOSED.load(Ordering::Relaxed), 3);
    FAIL_SECOND_EXPORT.store(true, Ordering::Relaxed);
    let packet = exporter.snapshot(None);
    assert!(packet.send(left.as_fd()));
    drop(packet);
    receive(-1);
    assert_eq!(std::fs::read_dir("/proc/self/fd").unwrap().count(), initial);
    assert_eq!(HANDLES_CLOSED.load(Ordering::Relaxed), 4);
    FAIL_SECOND_EXPORT.store(false, Ordering::Relaxed);
    REMOVE_PROPERTY.store(true, Ordering::Relaxed);
    let packet = exporter.snapshot(None);
    assert!(packet.send(left.as_fd()));
    drop(packet);
    receive(-1);
    REMOVE_PROPERTY.store(false, Ordering::Relaxed);
    CONNECTOR.store(32, Ordering::Relaxed);
    let packet = exporter.snapshot(None);
    assert!(packet.send(left.as_fd()));
    drop(packet);
    receive(1);
    assert!(DESCRIPTIONS.load(Ordering::Relaxed) > 15);
    assert_eq!(FRAMEBUFFERS.load(Ordering::Relaxed), 5);
    assert_eq!(HANDLES_CLOSED.load(Ordering::Relaxed), 5);
    assert_eq!(std::fs::read_dir("/proc/self/fd").unwrap().count(), initial);
    SECOND_CONNECTOR.store(true, Ordering::Relaxed);
    let packet = exporter.snapshot(Some(33));
    assert!(packet.send(left.as_fd()));
    drop(packet);
    let mut empty = ffi::gsr_kms_response::default();
    assert_eq!(
        // SAFETY: retained socket and one exclusive initialized response output.
        unsafe { transport::dpe_kms_receive(right.as_raw_fd(), &mut empty) },
        1
    );
    assert_eq!(empty.num_items, 0);
    assert_eq!(
        FRAMEBUFFERS.load(Ordering::Relaxed),
        5,
        "foreign outputs must not obtain GEM handles or exported fds"
    );
    let packet = exporter.snapshot(Some(999));
    assert!(packet.send(left.as_fd()));
    drop(packet);
    receive(-1);
    let packet = exporter.snapshot(Some(32));
    assert!(packet.send(left.as_fd()));
    drop(packet);
    receive(1);
    assert_eq!(FRAMEBUFFERS.load(Ordering::Relaxed), 6);
    assert_eq!(HANDLES_CLOSED.load(Ordering::Relaxed), 6);
    assert_eq!(std::fs::read_dir("/proc/self/fd").unwrap().count(), initial);
}
