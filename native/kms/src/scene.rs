// SPDX-License-Identifier: GPL-3.0-only
//! Scene policy returns checked indices. Raw pointers exist only in the C ABI
//! adapter and always point into the caller's still-borrowed response.
use crate::ffi;
use std::ptr;
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Scene {
    Unsupported,
    Changed,
    Valid {
        primary: usize,
        cursor: Option<usize>,
    },
}
const fn code(value: &[u8; 4]) -> u32 {
    u32::from_le_bytes(*value)
}
fn rgb(format: u32, cursor: bool) -> bool {
    [code(b"AR24"), code(b"AB24")].contains(&format)
        || (!cursor
            && [
                code(b"XR24"),
                code(b"XB24"),
                code(b"AR30"),
                code(b"AB30"),
                code(b"XR30"),
                code(b"XB30"),
            ]
            .contains(&format))
}
pub(crate) fn scene(
    response: &ffi::gsr_kms_response,
    output: &ffi::dpe_kms_output,
    layout: &ffi::dpe_kms_layout,
) -> Scene {
    if response.version != 6 || response.result != 0 || !(0..8).contains(&response.num_items) {
        return Scene::Unsupported;
    }
    let mut primary = None;
    let mut cursor = None;
    let mut count = 0;
    for (index, plane) in response.items[..response.num_items as usize]
        .iter()
        .enumerate()
    {
        if plane.connector_id == 0 {
            return Scene::Unsupported;
        }
        if plane.connector_id != output.connector {
            continue;
        }
        let is_cursor = plane.plane_type == ffi::gsr_kms_plane_type_KMS_PLANE_TYPE_CURSOR;
        if (!is_cursor && plane.plane_type != ffi::gsr_kms_plane_type_KMS_PLANE_TYPE_PRIMARY)
            || plane.rotation != 0
            || !(1..=4).contains(&plane.num_dma_bufs)
            || !rgb(plane.pixel_format, is_cursor)
            || !(1..=16384).contains(&plane.width)
            || !(1..=16384).contains(&plane.height)
            || plane.src_x < 0
            || plane.src_y < 0
            || plane.src_w <= 0
            || plane.src_h <= 0
            || i64::from(plane.src_x) + i64::from(plane.src_w) > i64::from(plane.width)
            || i64::from(plane.src_y) + i64::from(plane.src_h) > i64::from(plane.height)
            || !(1..=16384).contains(&plane.dst_w)
            || !(1..=16384).contains(&plane.dst_h)
            || !(-16384..=16384).contains(&plane.dst_x)
            || !(-16384..=16384).contains(&plane.dst_y)
        {
            return Scene::Unsupported;
        }
        if plane.has_hdr_metadata {
            if plane.hdr_metadata.metadata_type != 0 {
                return Scene::Unsupported;
            }
            // SAFETY: metadata_type selects this public Linux HDR metadata
            // record; eotf is an integer field admitting all input bit patterns.
            let hdr = unsafe { plane.hdr_metadata.__bindgen_anon_1.hdmi_metadata_type1 };
            if hdr.eotf != 0 {
                return Scene::Unsupported;
            }
        }
        if plane.dma_buf[..plane.num_dma_bufs as usize]
            .iter()
            .any(|p| p.fd < 0 || p.pitch == 0 || p.pitch > 262144 || p.offset > i32::MAX as u32)
        {
            return Scene::Unsupported;
        }
        let slot = if is_cursor { &mut cursor } else { &mut primary };
        if slot.replace(index).is_some() {
            return Scene::Unsupported;
        }
        count += 1;
    }
    if count != layout.planes
        || primary.is_none()
        || !layout.primary_present
        || cursor.is_some() != layout.cursor_present
    {
        return Scene::Changed;
    }
    let Some(index) = primary else {
        return Scene::Changed;
    };
    let plane = &response.items[index];
    if layout.primary_needs_opaque
        && ![code(b"XR24"), code(b"XB24"), code(b"XR30"), code(b"XB30")]
            .contains(&plane.pixel_format)
    {
        return Scene::Unsupported;
    }
    if plane.dst_x != 0
        || plane.dst_y != 0
        || plane.dst_w as u32 != output.width
        || plane.dst_h as u32 != output.height
        || cursor.is_some_and(|cursor| response.items[cursor].zpos <= plane.zpos)
    {
        return Scene::Unsupported;
    }
    Scene::Valid {
        primary: index,
        cursor,
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_scene(
    response: *const ffi::gsr_kms_response,
    output: *const ffi::dpe_kms_output,
    layout: *const ffi::dpe_kms_layout,
    selected: *mut ffi::dpe_kms_scene,
) -> i32 {
    if response.is_null() || output.is_null() || layout.is_null() || selected.is_null() {
        return -1;
    }
    // SAFETY: caller owns the output and retains all initialized input records
    // through its subsequent use of selected's borrowed pointers.
    let (response, output, layout, selected) =
        unsafe { (&*response, &*output, &*layout, &mut *selected) };
    *selected = ffi::dpe_kms_scene::default();
    match scene(response, output, layout) {
        Scene::Unsupported => -1,
        Scene::Changed => 0,
        Scene::Valid { primary, cursor } => {
            selected.primary = &response.items[primary];
            selected.cursor = cursor.map_or(ptr::null(), |index| &response.items[index]);
            1
        }
    }
}
