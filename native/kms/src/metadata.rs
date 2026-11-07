// SPDX-License-Identifier: GPL-3.0-only
//! Read-only monitor inventory and per-frame plane validation. Property IDs
//! and enum meanings are cached once; frame checks fetch current values only.
use crate::{drm, ffi};
use dpe_core::handles::Handles;
use std::{
    ffi::{CStr, c_void},
    fs::OpenOptions,
    os::{
        fd::BorrowedFd,
        unix::{ffi::OsStrExt, fs::OpenOptionsExt},
    },
    ptr,
    sync::{Arc, OnceLock},
};
const FIELDS: usize = 10;
const FB: usize = 0;
const CRTC: usize = 1;
const TYPE: usize = 2;
const ROTATION: usize = 3;
const ALPHA: usize = 4;
const BLEND: usize = 5;
const SX: usize = 6;
const SH: usize = 9;
const NAMES: [&[u8]; FIELDS] = [
    b"FB_ID",
    b"CRTC_ID",
    b"type",
    b"rotation",
    b"alpha",
    b"pixel blend mode",
    b"SRC_X",
    b"SRC_Y",
    b"SRC_W",
    b"SRC_H",
];
struct Plane {
    id: u32,
    fields: [u32; FIELDS],
    blend_none: Option<u64>,
    blend_premul: Option<u64>,
    blend_coverage: Option<u64>,
}
#[derive(Debug, PartialEq, Eq)]
enum Active {
    Hidden,
    Primary { needs_opaque: bool },
    Cursor,
}
impl Plane {
    fn classify(&self, values: &[Option<u64>; FIELDS], wanted_crtc: u32) -> Option<Active> {
        // Even disabled planes must retain both required identity properties.
        // Missing metadata is different from a known, inactive framebuffer.
        let framebuffer = values[FB]?;
        let active_crtc = values[CRTC]?;
        if framebuffer == 0 || active_crtc != u64::from(wanted_crtc) {
            return Some(Active::Hidden);
        }
        let kind = values[TYPE]?;
        let is_cursor = kind == u64::from(ffi::DRM_PLANE_TYPE_CURSOR);
        if !is_cursor && kind != u64::from(ffi::DRM_PLANE_TYPE_PRIMARY) {
            return None;
        }
        if values[ROTATION].is_some_and(|v| v != u64::from(ffi::DRM_MODE_ROTATE_0))
            || values[ALPHA].is_some_and(|v| v != 65535)
            || values[SX..=SH]
                .iter()
                .any(|v| v.is_none_or(|v| v & 65535 != 0))
        {
            return None;
        }
        let mut needs_opaque = false;
        if let Some(blend) = values[BLEND] {
            if is_cursor {
                if Some(blend) != self.blend_premul {
                    return None;
                }
            } else if Some(blend) != self.blend_none {
                if Some(blend) == self.blend_coverage {
                    needs_opaque = true;
                } else if Some(blend) != self.blend_premul {
                    return None;
                }
            }
        }
        Some(if is_cursor {
            Active::Cursor
        } else {
            Active::Primary { needs_opaque }
        })
    }
}
pub(crate) struct Inspector {
    fd: drm::Node,
    output: ffi::dpe_kms_output,
    connector_crtc: u32,
    planes: Vec<Plane>,
}
fn inspectors() -> &'static Handles<Arc<Inspector>> {
    static TABLE: OnceLock<Handles<Arc<Inspector>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::new(64))
}
impl Inspector {
    pub(crate) fn new(fd: drm::Node, output: ffi::dpe_kms_output) -> Option<Self> {
        let connector = drm::object(&fd, output.connector, ffi::DRM_MODE_OBJECT_CONNECTOR)?;
        let connector_crtc = connector.pairs()?.find_map(|(id, _)| {
            let property = drm::property(&fd, id)?;
            drm::named(&property.name, b"CRTC_ID").then_some(id)
        })?;
        let resources = drm::planes(&fd)?;
        let ids = resources.ids()?;
        let mut planes = Vec::with_capacity(ids.len());
        for &id in ids {
            let props = drm::object(&fd, id, ffi::DRM_MODE_OBJECT_PLANE)?;
            let mut plane = Plane {
                id,
                fields: [0; FIELDS],
                blend_none: None,
                blend_premul: None,
                blend_coverage: None,
            };
            for (id, _) in props.pairs()? {
                let prop = drm::property(&fd, id)?;
                for (field, &name) in NAMES.iter().enumerate() {
                    if drm::named(&prop.name, name) {
                        plane.fields[field] = id;
                    }
                }
                if drm::named(&prop.name, b"pixel blend mode") {
                    for item in prop.enums()? {
                        if drm::named(&item.name, b"None") {
                            plane.blend_none = Some(item.value);
                        } else if drm::named(&item.name, b"Pre-multiplied") {
                            plane.blend_premul = Some(item.value);
                        } else if drm::named(&item.name, b"Coverage") {
                            plane.blend_coverage = Some(item.value);
                        }
                    }
                }
            }
            if [FB, CRTC, TYPE]
                .iter()
                .any(|&field| plane.fields[field] == 0)
            {
                return None;
            }
            planes.push(plane);
        }
        Some(Self {
            fd,
            output,
            connector_crtc,
            planes,
        })
    }
    pub(crate) fn metadata(&self) -> Option<ffi::dpe_kms_layout> {
        let crtc = drm::crtc(&self.fd, self.output.crtc)?;
        if crtc.mode_valid == 0
            || crtc.width != self.output.width
            || crtc.height != self.output.height
        {
            return None;
        }
        let connector = drm::object(
            &self.fd,
            self.output.connector,
            ffi::DRM_MODE_OBJECT_CONNECTOR,
        )?;
        if connector.value(self.connector_crtc)? != u64::from(self.output.crtc) {
            return None;
        }
        let (mut primary, mut cursor) = (0u32, 0u32);
        let mut layout = ffi::dpe_kms_layout::default();
        for plane in &self.planes {
            let props = drm::object(&self.fd, plane.id, ffi::DRM_MODE_OBJECT_PLANE)?;
            let mut values = [None; FIELDS];
            for (id, value) in props.pairs()? {
                for (field, &wanted) in plane.fields.iter().enumerate() {
                    if wanted != 0 && id == wanted {
                        values[field] = Some(value);
                    }
                }
            }
            match plane.classify(&values, self.output.crtc)? {
                Active::Hidden => {}
                Active::Primary { needs_opaque } => {
                    primary += 1;
                    layout.primary_needs_opaque |= needs_opaque;
                }
                Active::Cursor => cursor += 1,
            }
        }
        if primary > 1 || cursor > 1 {
            return None;
        }
        layout.planes = primary + cursor;
        layout.primary_present = primary != 0;
        layout.cursor_present = cursor != 0;
        Some(layout)
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_inspector_create(
    fd: i32,
    output: *const ffi::dpe_kms_output,
) -> *mut c_void {
    if fd < 0 || output.is_null() {
        return ptr::null_mut();
    }
    // SAFETY: caller retains this fd while the constructor duplicates it.
    let Ok(owner) = unsafe { BorrowedFd::borrow_raw(fd) }.try_clone_to_owned() else {
        return ptr::null_mut();
    };
    let Some(owner) = drm::Node::new(owner) else {
        return ptr::null_mut();
    };
    // SAFETY: caller supplies one initialized output record for this construction.
    let Some(inspector) = Inspector::new(owner, unsafe { *output }) else {
        return ptr::null_mut();
    };
    inspectors()
        .insert(Arc::new(inspector))
        .map_or(ptr::null_mut(), |token| token.get() as *mut c_void)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_kms_inspector_destroy(handle: *mut c_void) {
    drop(inspectors().remove(handle as usize));
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_metadata(
    handle: *mut c_void,
    layout: *mut ffi::dpe_kms_layout,
) -> bool {
    if layout.is_null() {
        return false;
    }
    // SAFETY: caller provides the writable layout output.
    unsafe {
        layout.write(ffi::dpe_kms_layout::default());
    }
    let Some(inspector) = inspectors().get(handle as usize) else {
        return false;
    };
    let Some(current) = inspector.metadata() else {
        return false;
    };
    // SAFETY: publish only a fully checked layout to the caller's output.
    unsafe {
        layout.write(current);
    }
    true
}
fn copy_name<const N: usize>(target: &mut [libc::c_char; N], value: &[u8]) -> Option<()> {
    if value.len() >= N || value.contains(&0) {
        return None;
    }
    target.fill(0);
    for (to, &from) in target.iter_mut().zip(value) {
        *to = from as libc::c_char;
    }
    Some(())
}
fn inventory() -> Vec<ffi::dpe_kms_output> {
    let Some(devices) = drm::Devices::new() else {
        return Vec::new();
    };
    let mut outputs = Vec::with_capacity(32);
    for device in devices.iter() {
        let (Some(card), Some(render)) = (device.node(0), device.node(2)) else {
            continue;
        };
        let Ok(file) = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(std::ffi::OsStr::from_bytes(card.to_bytes()))
        else {
            continue;
        };
        let Some(fd) = drm::Node::new(file.into()) else {
            continue;
        };
        let atomic = fd.atomic_metadata();
        let Some(resources) = drm::resources(&fd) else {
            continue;
        };
        let Some(ids) = resources.connectors() else {
            continue;
        };
        for &id in ids {
            if outputs.len() == 32 {
                break;
            }
            let Some(connector) = drm::connector(&fd, id) else {
                continue;
            };
            if connector.connection != ffi::drmModeConnection_DRM_MODE_CONNECTED {
                continue;
            }
            let Some(properties) = drm::object(&fd, id, ffi::DRM_MODE_OBJECT_CONNECTOR) else {
                continue;
            };
            let crtc_id = properties.pairs().and_then(|mut pairs| {
                pairs.find_map(|(id, value)| {
                    let property = drm::property(&fd, id)?;
                    (drm::named(&property.name, b"CRTC_ID")
                        && value > 0
                        && value <= u64::from(u32::MAX))
                    .then_some(value as u32)
                })
            });
            let Some(crtc_id) = crtc_id else {
                continue;
            };
            let Some(crtc) = drm::crtc(&fd, crtc_id) else {
                continue;
            };
            if crtc.mode_valid == 0
                || !(1..=8192).contains(&crtc.width)
                || !(1..=8192).contains(&crtc.height)
            {
                continue;
            }
            let mut output = ffi::dpe_kms_output {
                connector: id,
                crtc: crtc_id,
                width: crtc.width,
                height: crtc.height,
                ..Default::default()
            };
            // SAFETY: libdrm returns a static NUL-terminated connector type name.
            let kind = unsafe { ffi::drmModeGetConnectorTypeName(connector.connector_type) };
            if kind.is_null() {
                continue;
            }
            // SAFETY: non-null type name is a static libdrm string.
            let kind = unsafe { CStr::from_ptr(kind) }.to_bytes();
            let base = card
                .to_bytes()
                .rsplit(|byte| *byte == b'/')
                .next()
                .unwrap_or(b"card");
            let mut identity = b"kms-".to_vec();
            identity.extend_from_slice(base);
            identity.extend_from_slice(format!("-{id}").as_bytes());
            let mut name = kind.to_vec();
            name.extend_from_slice(format!("-{}", connector.connector_type_id).as_bytes());
            if copy_name(&mut output.id, &identity).is_none()
                || copy_name(&mut output.name, &name).is_none()
                || copy_name(&mut output.card, card.to_bytes()).is_none()
                || copy_name(&mut output.render, render.to_bytes()).is_none()
            {
                continue;
            }
            if atomic {
                output.supported = fd
                    .try_clone()
                    .ok()
                    .and_then(|fd| Inspector::new(fd, output))
                    .and_then(|inspector| inspector.metadata())
                    .is_some_and(|layout| layout.planes > 0);
            }
            outputs.push(output);
        }
    }
    // Clone outputs map one CRTC to several connectors. Keep all visible in
    // inventory, but do not guess which connector the exporter will describe.
    for index in 0..outputs.len() {
        if outputs.iter().enumerate().any(|(other, value)| {
            other != index && value.card == outputs[index].card && value.crtc == outputs[index].crtc
        }) {
            outputs[index].supported = false;
        }
    }
    outputs
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_inventory(output: *mut ffi::dpe_kms_output) -> u32 {
    if output.is_null() {
        return 0;
    }
    let values = inventory();
    // SAFETY: public C ABI requires space for DPE_KMS_OUTPUTS (32) records;
    // inventory never exceeds that bound and publishes initialized records.
    unsafe {
        ptr::copy_nonoverlapping(values.as_ptr(), output, values.len());
    }
    values.len() as u32
}

#[cfg(test)]
mod tests {
    use super::*;
    fn plane() -> Plane {
        Plane {
            id: 9,
            fields: [1; FIELDS],
            blend_none: Some(0),
            blend_premul: Some(1),
            blend_coverage: Some(2),
        }
    }
    fn primary() -> [Option<u64>; FIELDS] {
        [
            Some(123),
            Some(95),
            Some(u64::from(ffi::DRM_PLANE_TYPE_PRIMARY)),
            Some(u64::from(ffi::DRM_MODE_ROTATE_0)),
            Some(65535),
            Some(1),
            Some(0),
            Some(0),
            Some(1920 << 16),
            Some(1080 << 16),
        ]
    }
    #[test]
    fn absent_identity_is_not_mistaken_for_an_inactive_plane() {
        let plane = plane();
        let mut values = primary();
        assert_eq!(
            plane.classify(&values, 95),
            Some(Active::Primary {
                needs_opaque: false
            })
        );
        values[FB] = Some(0);
        assert_eq!(plane.classify(&values, 95), Some(Active::Hidden));
        values[CRTC] = None;
        assert_eq!(plane.classify(&values, 95), None);
        values = primary();
        values[CRTC] = Some(96);
        values[ALPHA] = Some(1);
        assert_eq!(plane.classify(&values, 95), Some(Active::Hidden));
    }
    #[test]
    fn unsupported_composition_is_rejected_and_coverage_requires_opaque_rgb() {
        let plane = plane();
        for (field, value) in [
            (ROTATION, Some(2)),
            (ALPHA, Some(32)),
            (SX, Some(1)),
            (SH, None),
            (TYPE, Some(u64::from(ffi::DRM_PLANE_TYPE_OVERLAY))),
        ] {
            let mut values = primary();
            values[field] = value;
            assert_eq!(plane.classify(&values, 95), None);
        }
        let mut values = primary();
        values[BLEND] = Some(2);
        assert_eq!(
            plane.classify(&values, 95),
            Some(Active::Primary { needs_opaque: true })
        );
        values[TYPE] = Some(u64::from(ffi::DRM_PLANE_TYPE_CURSOR));
        assert_eq!(plane.classify(&values, 95), None);
        values[BLEND] = Some(1);
        assert_eq!(plane.classify(&values, 95), Some(Active::Cursor));
    }
}
