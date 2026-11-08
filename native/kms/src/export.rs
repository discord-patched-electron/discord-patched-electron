// SPDX-License-Identifier: GPL-3.0-only
//! GSR-compatible DRM snapshots. Immutable property descriptions are cached;
//! values and framebuffer handles are always read afresh. Never cache FB IDs
//! as allocation identity: the kernel may reuse both IDs and GEM handles.
use crate::{drm, ffi};
use dpe_os::{profiler::Span, wire};
use std::{
    mem::offset_of,
    os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
};
const SIZE: usize = 1424;
const ITEM: usize = 136;
const STRIDE: usize = 160;
const HDR_SIZE: usize = size_of::<ffi::hdr_output_metadata>();
const _: () = assert!(HDR_SIZE == 32);
pub(crate) const SCOPED_CAPABILITY: &[u8] = b"DPE-KMS-SCOPE-1";
pub(crate) struct Packet {
    bytes: [u8; SIZE],
    descriptors: [Option<OwnedFd>; 32],
    count: usize,
    items: usize,
}
impl Packet {
    pub fn new() -> Self {
        let mut packet = Self {
            bytes: [0; SIZE],
            descriptors: std::array::from_fn(|_| None),
            count: 0,
            items: 0,
        };
        packet.bytes[..4].copy_from_slice(&6u32.to_ne_bytes());
        packet
    }
    pub fn capabilities() -> Self {
        let mut packet = Self::new();
        packet.bytes[8..8 + SCOPED_CAPABILITY.len()].copy_from_slice(SCOPED_CAPABILITY);
        packet
    }
    fn error(message: &[u8]) -> Self {
        let mut packet = Self::new();
        packet.bytes[4..8].copy_from_slice(&2i32.to_ne_bytes());
        let count = message.len().min(127);
        packet.bytes[8..8 + count].copy_from_slice(&message[..count]);
        packet
    }
    fn push(
        &mut self,
        item: &ffi::gsr_kms_response_item,
        owners: [Option<OwnedFd>; 4],
        hdr: Option<&[u8; HDR_SIZE]>,
    ) -> Option<()> {
        let planes = owners.iter().flatten().count();
        if self.items == 8 || planes == 0 || owners[..planes].iter().any(Option::is_none) {
            return None;
        }
        let offset = ITEM + self.items * STRIDE;
        let bytes = &mut self.bytes[offset..offset + STRIDE];
        // Serialize fields into zeroed bytes, never Rust record padding. This
        // privileged protocol cannot expose uninitialized stack contents.
        macro_rules! put {
            ($field:ident) => {{
                let at = offset_of!(ffi::gsr_kms_response_item, $field);
                let value = item.$field.to_ne_bytes();
                bytes[at..at + value.len()].copy_from_slice(&value);
            }};
        }
        for (index, plane) in item.dma_buf.iter().enumerate().take(planes) {
            let at = index * 12;
            bytes[at..at + 4].copy_from_slice(&owners[index].as_ref()?.as_raw_fd().to_ne_bytes());
            bytes[at + 4..at + 8].copy_from_slice(&plane.pitch.to_ne_bytes());
            bytes[at + 8..at + 12].copy_from_slice(&plane.offset.to_ne_bytes());
        }
        bytes[48..52].copy_from_slice(&(planes as i32).to_ne_bytes());
        put!(width);
        put!(height);
        put!(pixel_format);
        put!(modifier);
        put!(connector_id);
        put!(plane_type);
        put!(rotation);
        put!(src_x);
        put!(src_y);
        put!(src_w);
        put!(src_h);
        put!(dst_x);
        put!(dst_y);
        put!(dst_w);
        put!(dst_h);
        put!(zpos);
        if let Some(hdr) = hdr {
            bytes[offset_of!(ffi::gsr_kms_response_item, has_hdr_metadata)] = 1;
            let at = offset_of!(ffi::gsr_kms_response_item, hdr_metadata);
            bytes[at..at + HDR_SIZE].copy_from_slice(hdr);
        }
        for owner in owners.into_iter().flatten() {
            self.descriptors[self.count] = Some(owner);
            self.count += 1;
        }
        self.items += 1;
        self.bytes[1416..1420].copy_from_slice(&(self.items as i32).to_ne_bytes());
        Some(())
    }
    pub fn send(&self, socket: BorrowedFd<'_>) -> bool {
        let mut descriptors = [socket; 32];
        for (to, owner) in descriptors
            .iter_mut()
            .zip(self.descriptors.iter().flatten())
        {
            *to = owner.as_fd();
        }
        wire::send_raw(socket, &self.bytes, &descriptors[..self.count]).is_ok()
    }
}
struct Fields<const N: usize> {
    id: u32,
    kind: u32,
    properties: [u32; N],
}
impl<const N: usize> Fields<N> {
    fn new(node: &drm::Node, id: u32, kind: u32, names: [&[u8]; N]) -> Option<Self> {
        let values = drm::object(node, id, kind)?;
        let mut properties = [0; N];
        for (id, _) in values.pairs()? {
            let record = drm::property(node, id)?;
            for (index, name) in names.iter().enumerate() {
                if drm::named(&record.name, name) {
                    properties[index] = id;
                }
            }
        }
        Some(Self {
            id,
            kind,
            properties,
        })
    }
    fn values(&self, node: &drm::Node) -> Option<[Option<u64>; N]> {
        let current = drm::object(node, self.id, self.kind)?;
        let mut values = [None; N];
        for (id, value) in current.pairs()? {
            for (index, &wanted) in self.properties.iter().enumerate() {
                if wanted != 0 && wanted == id {
                    values[index] = Some(value);
                }
            }
        }
        // A disappeared property invalidates the snapshot instead of using
        // stale values. The next request rebuilds this bounded description.
        for (index, id) in self.properties.iter().enumerate() {
            if *id != 0 && values[index].is_none() {
                return None;
            }
        }
        Some(values)
    }
}
const PLANE_NAMES: [&[u8]; 13] = [
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
struct Topology {
    connectors: Vec<Fields<2>>,
    planes: Vec<Fields<13>>,
}
impl Topology {
    fn new(node: &drm::Node, connector_ids: &[u32], plane_ids: &[u32]) -> Option<Self> {
        if connector_ids.len() > 32 || plane_ids.len() > 64 {
            return None;
        }
        let mut connectors = Vec::with_capacity(connector_ids.len());
        let mut planes = Vec::with_capacity(plane_ids.len());
        for &id in connector_ids {
            connectors.push(Fields::new(
                node,
                id,
                ffi::DRM_MODE_OBJECT_CONNECTOR,
                [b"CRTC_ID", b"HDR_OUTPUT_METADATA"],
            )?);
        }
        for &id in plane_ids {
            let mut fields = Fields::new(node, id, ffi::DRM_MODE_OBJECT_PLANE, PLANE_NAMES)?;
            if fields.properties[12] == 0 {
                let alias = Fields::new(node, id, ffi::DRM_MODE_OBJECT_PLANE, [b"ZPOS"])?;
                fields.properties[12] = alias.properties[0];
            }
            if fields.properties[..3].contains(&0) {
                return None;
            }
            planes.push(fields);
        }
        Some(Self { connectors, planes })
    }
    fn matches(&self, connector_ids: &[u32], plane_ids: &[u32]) -> bool {
        self.connectors
            .iter()
            .map(|v| v.id)
            .eq(connector_ids.iter().copied())
            && self
                .planes
                .iter()
                .map(|v| v.id)
                .eq(plane_ids.iter().copied())
    }
}
#[derive(Default, Copy, Clone)]
struct Connector {
    id: u32,
    crtc: u64,
    hdr: u64,
}
pub(crate) struct Exporter {
    node: drm::Node,
    topology: Option<Topology>,
}
impl Exporter {
    pub fn new(node: drm::Node) -> Self {
        Self {
            node,
            topology: None,
        }
    }
    pub fn snapshot(&mut self, selected: Option<u32>) -> Packet {
        let _profile = Span::new(27); // DPE_PROFILE_KMS_EXPORT, separate service process.
        let result = self.capture(selected);
        match result {
            Ok(packet) => packet,
            Err(message) => {
                self.topology = None;
                Packet::error(message)
            }
        }
    }
    fn capture(&mut self, selected: Option<u32>) -> Result<Packet, &'static [u8]> {
        let invalid = b"incomplete DRM snapshot".as_slice();
        let resources = drm::resources(&self.node).ok_or(invalid)?;
        let planes = drm::planes(&self.node).ok_or(invalid)?;
        let connector_ids = resources.connectors().ok_or(invalid)?;
        let plane_ids = planes.ids().ok_or(invalid)?;
        if self
            .topology
            .as_ref()
            .is_none_or(|t| !t.matches(connector_ids, plane_ids))
        {
            self.topology =
                Some(Topology::new(&self.node, connector_ids, plane_ids).ok_or(invalid)?);
        }
        let topology = self.topology.as_ref().ok_or(invalid)?;
        let mut connectors = [Connector::default(); 32];
        for (current, description) in connectors.iter_mut().zip(&topology.connectors) {
            let values = description.values(&self.node).ok_or(invalid)?;
            *current = Connector {
                id: description.id,
                crtc: values[0].unwrap_or(0),
                hdr: values[1].unwrap_or(0),
            };
        }
        if selected.is_some_and(|id| {
            !connectors[..topology.connectors.len()]
                .iter()
                .any(|c| c.id == id && c.crtc != 0)
        }) {
            return Err(b"selected connector is unavailable");
        }
        let mut packet = Packet::new();
        for description in &topology.planes {
            let values = description.values(&self.node).ok_or(invalid)?;
            let framebuffer = values[0].ok_or(invalid)?;
            let crtc = values[1].ok_or(invalid)?;
            if framebuffer == 0 || crtc == 0 {
                continue;
            }
            let connector = connectors[..topology.connectors.len()]
                .iter()
                .find(|c| c.crtc == crtc)
                .ok_or(invalid)?;
            if selected.is_some_and(|id| id != connector.id) {
                continue;
            }
            // GETFB2 is deliberately fresh. Each result owns new GEM handles;
            // Framebuffer closes distinct handles even on partial export errors.
            let Some(fb) = u32::try_from(framebuffer)
                .ok()
                .and_then(|id| drm::framebuffer(&self.node, id))
            else {
                continue;
            };
            if fb.handles[0] == 0 {
                return Err(b"drmfb handle is NULL");
            }
            let mut item = geometry(&values, fb.width, fb.height).ok_or(invalid)?;
            item.width = fb.width;
            item.height = fb.height;
            item.pixel_format = fb.pixel_format;
            item.modifier = if fb.flags & ffi::DRM_MODE_FB_MODIFIERS != 0 {
                fb.modifier
            } else {
                (1u64 << 56) - 1
            };
            item.connector_id = connector.id;
            let mut owners: [Option<OwnedFd>; 4] = std::array::from_fn(|_| None);
            for (index, &handle) in fb.handles.iter().enumerate() {
                if handle == 0 {
                    if fb.handles[index..].iter().any(|h| *h != 0) {
                        return Err(invalid);
                    }
                    break;
                }
                let mut raw = -1;
                // SAFETY: verified DRM node, freshly owned GEM handle, one fd
                // output. CLOEXEC is requested atomically on every export.
                if unsafe {
                    ffi::drmPrimeHandleToFD(
                        self.node.as_raw_fd(),
                        handle,
                        (libc::O_RDONLY | libc::O_CLOEXEC) as u32,
                        &mut raw,
                    )
                } != 0
                    || raw < 0
                {
                    return Err(b"DRM PRIME export failed");
                }
                // SAFETY: successful PRIME ioctl transferred a unique fd.
                owners[index] = Some(unsafe { OwnedFd::from_raw_fd(raw) });
                item.dma_buf[index].pitch = fb.pitches[index];
                item.dma_buf[index].offset = fb.offsets[index];
            }
            let mut hdr = [0u8; HDR_SIZE];
            let has_hdr = connector.hdr != 0;
            if has_hdr {
                let blob = drm::blob(
                    &self.node,
                    u32::try_from(connector.hdr).map_err(|_| invalid)?,
                )
                .ok_or(invalid)?;
                let bytes = blob.bytes().ok_or(invalid)?;
                hdr.copy_from_slice(bytes.get(..HDR_SIZE).ok_or(invalid)?);
            }
            packet
                .push(&item, owners, has_hdr.then_some(&hdr))
                .ok_or(b"too many DRM planes".as_slice())?;
        }
        Ok(packet)
    }
}
fn geometry(
    values: &[Option<u64>; 13],
    width: u32,
    height: u32,
) -> Option<ffi::gsr_kms_response_item> {
    let kind = values[2]?;
    let plane_type = if kind == u64::from(ffi::DRM_PLANE_TYPE_PRIMARY) {
        ffi::gsr_kms_plane_type_KMS_PLANE_TYPE_PRIMARY
    } else if kind == u64::from(ffi::DRM_PLANE_TYPE_CURSOR) {
        ffi::gsr_kms_plane_type_KMS_PLANE_TYPE_CURSOR
    } else if kind == u64::from(ffi::DRM_PLANE_TYPE_OVERLAY) {
        ffi::gsr_kms_plane_type_KMS_PLANE_TYPE_OVERLAY
    } else {
        return None;
    };
    let fixed = |index: usize, fallback: u32| -> Option<i32> {
        let raw = values[index].unwrap_or(u64::from(fallback) << 16);
        // GSR's wire rectangle is integer-valued. Selected-output metadata
        // validation rejects fractional crops; an unrelated output must not
        // invalidate this legacy all-output response merely for having one.
        i32::try_from(raw >> 16).ok()
    };
    let signed =
        |index: usize| -> Option<i32> { i32::try_from(values[index].unwrap_or(0) as i64).ok() };
    let src_w = fixed(6, width)?;
    let src_h = fixed(7, height)?;
    // Reflection/unknown rotation is preserved as unsupported, never silently
    // converted into an unrotated picture. The client validates scene policy.
    let rotation = match values[3].unwrap_or(1) {
        1 => 0,
        2 => 1,
        4 => 2,
        8 => 3,
        _ => 4,
    };
    Some(ffi::gsr_kms_response_item {
        plane_type,
        rotation,
        src_x: fixed(4, 0)?,
        src_y: fixed(5, 0)?,
        src_w,
        src_h,
        dst_x: signed(8)?,
        dst_y: signed(9)?,
        dst_w: i32::try_from(values[10].unwrap_or(src_w as u64)).ok()?,
        dst_h: i32::try_from(values[11].unwrap_or(src_h as u64)).ok()?,
        zpos: if values[12].is_some() {
            signed(12)?
        } else {
            match plane_type {
                ffi::gsr_kms_plane_type_KMS_PLANE_TYPE_PRIMARY => 0,
                ffi::gsr_kms_plane_type_KMS_PLANE_TYPE_CURSOR => 2,
                _ => 1,
            }
        },
        ..Default::default()
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn geometry_preserves_signed_positions_and_wire_rounding_but_rejects_overflow() {
        let mut values = [None; 13];
        values[2] = Some(u64::from(ffi::DRM_PLANE_TYPE_CURSOR));
        values[8] = Some((-12i64) as u64);
        let item = geometry(&values, 64, 32).unwrap();
        assert_eq!(
            (item.dst_x, item.src_w, item.src_h, item.zpos),
            (-12, 64, 32, 2)
        );
        values[3] = Some(17);
        assert_eq!(geometry(&values, 64, 32).unwrap().rotation, 4);
        values[4] = Some(1);
        assert_eq!(geometry(&values, 64, 32).unwrap().src_x, 0);
        values[4] = None;
        values[10] = Some(u64::MAX);
        assert!(geometry(&values, 64, 32).is_none());
    }
    #[test]
    fn packets_initialize_padding_and_retain_descriptor_ownership_until_send_completion() {
        let _exclusive = crate::FD_TESTS.lock().unwrap();
        use std::os::unix::net::UnixDatagram;
        let (left, right) = UnixDatagram::pair().unwrap();
        let item = ffi::gsr_kms_response_item {
            width: 123,
            height: 45,
            ..Default::default()
        };
        let mut packet = Packet::new();
        packet
            .push(
                &item,
                [
                    Some(left.as_fd().try_clone_to_owned().unwrap()),
                    None,
                    None,
                    None,
                ],
                None,
            )
            .unwrap();
        assert!(packet.send(left.as_fd()));
        let mut bytes = [0u8; SIZE];
        let reply = wire::receive(right.as_fd(), &mut bytes, 32)
            .unwrap()
            .unwrap();
        assert_eq!(reply.count, 1);
        assert_eq!(bytes[1416..1420], 1i32.to_ne_bytes());
        assert_eq!(&bytes[ITEM + 81..ITEM + 84], &[0; 3]); // C alignment padding.
        assert_eq!(&bytes[ITEM + 124..ITEM + 128], &[0; 4]);
        assert_eq!(&bytes[1420..], &[0; 4]);
        assert_eq!(&bytes[ITEM + STRIDE..1416], &[0; 7 * STRIDE]);
    }
}
