// SPDX-License-Identifier: GPL-3.0-only
//! Typed game surfaces. Imported textures and output surfaces retain their
//! renderer independently; the caller owns completion fences and hook leases.
use crate::{
    context::Context,
    ffi as f,
    game_render::{CursorOwner, Input, Renderer, device_uuid},
    target::Target,
};
use dpe_game::protocol::Packet;
use std::{
    cell::RefCell,
    ffi::CStr,
    os::fd::{AsFd, AsRawFd, BorrowedFd, OwnedFd},
    rc::Rc,
};
pub fn fence_complete(descriptor: BorrowedFd<'_>) -> std::io::Result<bool> {
    let mut info = f::sync_file_info::default();
    // SAFETY: the borrowed descriptor remains live; this documented initialized
    // UAPI record requests status only, without a foreign fence-array pointer.
    if unsafe {
        libc::ioctl(
            descriptor.as_raw_fd(),
            libc::_IOWR::<f::sync_file_info>(b'>' as u32, 4),
            &mut info,
        )
    } != 0
    {
        return Err(std::io::Error::last_os_error());
    }
    if info.status < 0 {
        return Err(std::io::Error::from_raw_os_error(
            info.status.checked_neg().unwrap_or(libc::EIO),
        ));
    }
    Ok(info.status > 0)
}
pub struct Gpu {
    context: Rc<Context>,
    renderer: RefCell<Option<Renderer>>,
    cursor: RefCell<Option<CursorOwner>>,
    uuid: [u8; 16],
}
pub struct Texture {
    input: Input,
    owner: Rc<Gpu>,
    size: (u32, u32),
    flip: bool,
    window: u32,
}
pub struct Surface {
    target: Target,
    owner: Rc<Gpu>,
}
pub struct Export<'a> {
    pub descriptor: BorrowedFd<'a>,
    pub width: u32,
    pub height: u32,
    pub stride: u32,
    pub format: u32,
    pub modifier: u64,
}
impl Gpu {
    pub fn open(node: &CStr) -> Option<Rc<Self>> {
        let context = Context::open(node)?;
        let uuid = device_uuid(&context)?;
        Some(Rc::new(Self {
            context,
            renderer: RefCell::new(None),
            cursor: RefCell::new(None),
            uuid,
        }))
    }
    pub fn uuid(&self) -> [u8; 16] {
        self.uuid
    }
    pub fn device(&self) -> BorrowedFd<'_> {
        self.context.file.as_fd()
    }
    pub fn prepare_copy(&self) -> bool {
        let Ok(mut renderer) = self.renderer.try_borrow_mut() else {
            return false;
        };
        if renderer.is_none() {
            *renderer = Renderer::new(&self.context);
        }
        renderer.is_some()
    }
    pub fn import(
        self: &Rc<Self>,
        packet: &Packet,
        descriptors: &[BorrowedFd<'_>],
    ) -> Option<Texture> {
        if descriptors.len() != packet.nfd as usize
            || !(1..=4).contains(&descriptors.len())
            || !self.context.current()
        {
            return None;
        }
        let mut fds = [-1; 4];
        for (target, fd) in fds.iter_mut().zip(descriptors) {
            *target = fd.as_raw_fd();
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
        let input = Input::new(&self.context, &native, &fds)?;
        Some(Texture {
            input,
            owner: self.clone(),
            size: (packet.width, packet.height),
            flip: packet.flags & dpe_game::protocol::FLIP != 0,
            window: u32::from_ne_bytes(packet.name[..4].try_into().ok()?),
        })
    }
    pub fn allocate(self: &Rc<Self>, width: u32, height: u32) -> Option<Surface> {
        Some(Surface {
            target: Target::new(&self.context, width, height)?,
            owner: self.clone(),
        })
    }
}
impl Texture {
    pub fn dimensions(&self) -> (u32, u32) {
        self.size
    }
}
impl Surface {
    pub fn export(&self) -> Export<'_> {
        Export {
            descriptor: self.target.export.as_fd(),
            width: self.target.width,
            height: self.target.height,
            stride: self.target.stride,
            format: f::DPE_GRAPHICS_ARGB,
            modifier: 0,
        }
    }
    pub fn render(&self, texture: &Texture, paint_cursor: bool) -> Option<OwnedFd> {
        if !Rc::ptr_eq(&self.owner, &texture.owner)
            || texture.size != (self.target.width, self.target.height)
            || !self.owner.prepare_copy()
            || !self.target.bind()
        {
            return None;
        }
        let mut renderer = self.owner.renderer.try_borrow_mut().ok()?;
        let renderer = renderer.as_mut()?;
        if !renderer.draw(&texture.input, texture.flip, texture.size) {
            return None;
        }
        if paint_cursor {
            self.owner
                .cursor
                .try_borrow_mut()
                .ok()?
                .get_or_insert_with(CursorOwner::new)
                .draw(renderer, texture.window, texture.size);
        }
        // SAFETY: the retained renderer context is current; a scalar error
        // query validates drawing before exporting its owned completion fence.
        if unsafe { f::glGetError() } != f::GL_NO_ERROR {
            return None;
        }
        renderer.fence()
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "requires explicitly selected DPE_GRAPHICS_TEST_NODE"]
    fn imported_texture_and_completed_surface_keep_their_renderer_alive() {
        let node = std::ffi::CString::new(
            std::env::var("DPE_GRAPHICS_TEST_NODE").expect("explicit owned GPU test"),
        )
        .unwrap();
        let gpu = Gpu::open(&node).unwrap();
        let input = gpu.allocate(64, 32).unwrap();
        let output = gpu.allocate(64, 32).unwrap();
        assert!(input.target.bind());
        // SAFETY: only this owned 64x32 synthetic input framebuffer is current.
        unsafe {
            f::glClearColor(0.25, 0.5, 0.75, 1.0);
            f::glClear(f::GL_COLOR_BUFFER_BIT);
        }
        let export = input.export();
        let packet = Packet {
            width: export.width,
            height: export.height,
            format: export.format,
            modifier: export.modifier,
            nfd: 1,
            strides: [export.stride, 0, 0, 0],
            ..Packet::new(dpe_game::protocol::TEXTURE)
        };
        let texture = gpu.import(&packet, &[export.descriptor]).unwrap();
        drop(gpu);
        let fence = output.render(&texture, false).unwrap();
        drop(texture);
        drop(input);
        let mut poll = libc::pollfd {
            fd: fence.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        // SAFETY: one initialized descriptor record borrows this owned fence.
        assert_eq!(unsafe { libc::poll(&mut poll, 1, 1000) }, 1);
        assert!(fence_complete(fence.as_fd()).unwrap());
        let mut pixels = vec![0; 64 * 32 * 4];
        assert!(output.target.read_bgra(&mut pixels));
        for pixel in pixels.as_chunks::<4>().0 {
            for (&actual, expected) in pixel.iter().zip([191u8, 128, 64, 255]) {
                assert!(actual.abs_diff(expected) <= 1);
            }
        }
    }
}
