// SPDX-License-Identifier: GPL-3.0-only
//! Typed renderer for Rust KMS consumers. Exported descriptors borrow their
//! surface; each surface retains its program/context independently of Renderer.
use crate::{
    context::Context,
    ffi as f, kms,
    target::{Program, Target},
};
use dpe_kms::capture::{Frame, Image};
use std::{
    cell::RefCell,
    ffi::CStr,
    os::fd::{AsFd, AsRawFd, BorrowedFd, OwnedFd},
    rc::Rc,
};
pub struct Renderer {
    program: Rc<Program>,
    preview: RefCell<Option<Target>>,
}
pub struct Surface {
    target: Target,
    program: Rc<Program>,
}
pub struct Export<'a> {
    pub width: u32,
    pub height: u32,
    pub format: u32,
    pub modifier: u64,
    pub stride: u32,
    pub descriptor: BorrowedFd<'a>,
}
impl Renderer {
    pub fn open(node: &CStr) -> Option<Self> {
        let context = Context::open(node)?;
        Some(Self {
            program: Rc::new(Program::new(&context)?),
            preview: RefCell::new(None),
        })
    }
    pub fn device(&self) -> BorrowedFd<'_> {
        self.program.context.file.as_fd()
    }
    pub fn allocate(&self, width: u32, height: u32) -> Option<Surface> {
        Some(Surface {
            target: Target::new(&self.program.context, width, height)?,
            program: self.program.clone(),
        })
    }
    pub fn thumbnail(
        &self,
        primary: &Image<'_>,
        cursor: Option<&Image<'_>>,
        canvas: (u32, u32),
        requested: (u32, u32),
        pixels: &mut [u8],
    ) -> Option<(u32, u32)> {
        let size = kms::fit(canvas, requested)?;
        let primary = native(primary)?;
        let cursor = match cursor {
            Some(cursor) => Some(native(cursor)?),
            None => None,
        };
        let mut preview = self.preview.try_borrow_mut().ok()?;
        if preview
            .as_ref()
            .is_none_or(|target| (target.width, target.height) != size)
        {
            *preview = Some(Target::new(&self.program.context, size.0, size.1)?);
        }
        let target = preview.as_ref()?;
        if !kms::render(target, &self.program, &primary, cursor.as_ref(), canvas) {
            return None;
        }
        target
            .read_bgra(pixels.get_mut(..size.0 as usize * size.1 as usize * 4)?)
            .then_some(size)
    }
}
fn native(image: &Image<'_>) -> Option<f::gsr_kms_response_item> {
    let mut item = f::gsr_kms_response_item {
        width: image.width,
        height: image.height,
        pixel_format: image.format,
        modifier: image.modifier,
        src_x: image.source.x,
        src_y: image.source.y,
        src_w: image.source.width.try_into().ok()?,
        src_h: image.source.height.try_into().ok()?,
        dst_x: image.destination.x,
        dst_y: image.destination.y,
        dst_w: image.destination.width.try_into().ok()?,
        dst_h: image.destination.height.try_into().ok()?,
        ..Default::default()
    };
    let mut ended = false;
    for (output, plane) in item.dma_buf.iter_mut().zip(image.planes) {
        if let Some(plane) = plane {
            if ended {
                return None;
            }
            *output = f::gsr_kms_response_dma_buf {
                fd: plane.descriptor.as_raw_fd(),
                pitch: plane.stride,
                offset: plane.offset,
            };
            item.num_dma_bufs += 1;
        } else {
            ended = true;
        }
    }
    (item.num_dma_bufs != 0).then_some(item)
}
impl Surface {
    pub fn export(&self) -> Export<'_> {
        Export {
            width: self.target.width,
            height: self.target.height,
            format: f::DPE_GRAPHICS_ARGB,
            modifier: 0,
            stride: self.target.stride,
            descriptor: self.target.export.as_fd(),
        }
    }
    pub fn render(&self, frame: &Frame) -> Option<OwnedFd> {
        self.render_images(&frame.primary(), frame.cursor().as_ref())
    }
    pub fn render_images(
        &self,
        primary: &Image<'_>,
        cursor: Option<&Image<'_>>,
    ) -> Option<OwnedFd> {
        let primary = native(primary)?;
        let cursor = if let Some(cursor) = cursor {
            Some(native(cursor)?)
        } else {
            None
        };
        if !kms::render(
            &self.target,
            &self.program,
            &primary,
            cursor.as_ref(),
            (self.target.width, self.target.height),
        ) {
            return None;
        }
        self.program.fence()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use dpe_core::geometry::Rect;
    use dpe_kms::capture::Plane;
    #[test]
    #[ignore = "requires explicitly selected DPE_GRAPHICS_TEST_NODE"]
    fn completed_surface_and_borrowed_exports_outlive_renderer() {
        let node = std::env::var("DPE_GRAPHICS_TEST_NODE").expect("explicit test node");
        let node = std::ffi::CString::new(node).unwrap();
        let renderer = Renderer::open(&node).expect("owned renderer");
        let input = renderer.allocate(64, 32).unwrap();
        let output = renderer.allocate(64, 32).unwrap();
        assert!(input.target.bind());
        // SAFETY: the owned input framebuffer/context are current on this test
        // thread, and only its synthetic solid color is drawn or read back.
        unsafe {
            f::glClearColor(0.25, 0.5, 0.75, 1.0);
            f::glClear(f::GL_COLOR_BUFFER_BIT);
        }
        drop(renderer);
        let export = input.export();
        let rect = Rect {
            x: 0,
            y: 0,
            width: 64,
            height: 32,
        };
        let image = Image {
            width: 64,
            height: 32,
            format: export.format,
            modifier: export.modifier,
            source: rect,
            destination: rect,
            planes: [
                Some(Plane {
                    descriptor: export.descriptor,
                    stride: export.stride,
                    offset: 0,
                }),
                None,
                None,
                None,
            ],
        };
        let fence = output
            .render_images(&image, None)
            .expect("owned completion fence");
        let mut poll = libc::pollfd {
            fd: fence.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        // SAFETY: one initialized poll record borrows this owned synthetic fence.
        let ready = unsafe { libc::poll(&mut poll, 1, 1000) };
        assert_eq!(ready, 1);
        let mut pixels = vec![0; 64 * 32 * 4];
        assert!(output.target.read_bgra(&mut pixels));
        for pixel in pixels.as_chunks::<4>().0 {
            for (&actual, expected) in pixel.iter().zip([191u8, 128, 64, 255]) {
                assert!(actual.abs_diff(expected) <= 1);
            }
        }
    }
}
