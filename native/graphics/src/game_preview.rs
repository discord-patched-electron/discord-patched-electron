// SPDX-License-Identifier: GPL-3.0-only
//! Safe thumbnail owner: no PipeWire loop, cursor connection or encoder.
use crate::{
    context::Context,
    ffi as f,
    game_render::{Input, Renderer, Thumbnail, device_uuid},
};
use dpe_game::protocol::Packet;
use std::{
    ffi::CStr,
    os::fd::{AsRawFd, BorrowedFd},
};
pub fn fence_signaled(fd: BorrowedFd<'_>) -> bool {
    let mut info = f::sync_file_info::default();
    // SAFETY: borrowed live descriptor; fixed initialized UAPI output requests
    // no foreign fence array. Readability alone never proves successful work.
    unsafe {
        libc::ioctl(
            fd.as_raw_fd(),
            libc::_IOWR::<f::sync_file_info>(b'>' as u32, 4),
            &mut info,
        ) == 0
            && info.status > 0
    }
}
pub struct Preview {
    renderer: Renderer,
    uuid: [u8; 16],
    input: Option<Input>,
    source: (u32, u32),
    flip: bool,
    target: Option<Thumbnail>,
}
impl Preview {
    pub fn open(node: &CStr) -> Option<Self> {
        let context = Context::open(node)?;
        let renderer = Renderer::new(&context)?;
        let uuid = device_uuid(&context)?;
        Some(Self {
            renderer,
            uuid,
            input: None,
            source: (0, 0),
            flip: false,
            target: None,
        })
    }
    pub fn uuid(&self) -> [u8; 16] {
        self.uuid
    }
    pub fn import(&mut self, packet: &Packet, descriptors: &[BorrowedFd<'_>]) -> bool {
        if descriptors.len() != packet.nfd as usize
            || descriptors.len() > 4
            || !self.renderer.context.current()
        {
            return false;
        }
        let mut fds = [-1; 4];
        for (slot, fd) in fds.iter_mut().zip(descriptors) {
            *slot = fd.as_raw_fd();
        }
        let native = f::dpe_vk_packet {
            width: packet.width,
            height: packet.height,
            format: packet.format,
            modifier: packet.modifier,
            flags: packet.flags,
            nfd: packet.nfd,
            strides: packet.strides,
            offsets: packet.offsets,
            ..Default::default()
        };
        let Some(input) = Input::new(&self.renderer.context, &native, &fds) else {
            return false;
        };
        self.input = Some(input);
        self.source = (packet.width, packet.height);
        self.flip = packet.flags & dpe_game::protocol::FLIP != 0;
        true
    }
    pub fn render(&mut self, requested: (u32, u32), pixels: &mut [u8]) -> Option<(u32, u32)> {
        let size = crate::kms::fit(self.source, requested)?;
        if !self.renderer.context.current() {
            return None;
        }
        let input = self.input.as_ref()?;
        if self.target.as_ref().is_none_or(|t| t.size != size) {
            self.target = Thumbnail::new(&self.renderer.context, size);
        }
        let target = self.target.as_ref()?;
        target.bind();
        if !self.renderer.draw(input, self.flip, size) {
            return None;
        }
        target
            .read(pixels.get_mut(..size.0 as usize * size.1 as usize * 4)?)
            .then_some(size)
    }
}
