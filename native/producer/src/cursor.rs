// SPDX-License-Identifier: GPL-3.0-only
//! Cursor owners stay on the producer thread. Only shape changes write a small,
//! sealed bitmap; position updates reuse its descriptor without copying pixels.
use crate::system;
use dpe_core::{
    direct::{self, Image},
    geometry::Rect,
};
use std::{
    ffi::CString,
    io,
    os::fd::{AsFd, BorrowedFd, OwnedFd},
};
struct Shape {
    serial: u32,
    width: u32,
    height: u32,
    fd: OwnedFd,
}
#[derive(Default)]
pub struct Cursor {
    x11: Option<usize>,
    wayland: Option<usize>,
    shape: Option<Shape>,
    _thread: std::marker::PhantomData<std::rc::Rc<()>>,
}
impl Drop for Cursor {
    fn drop(&mut self) {
        if let Some(handle) = self.x11 {
            dpe_x11::dpe_x11_cursor_destroy(handle as *mut _);
        }
        if let Some(handle) = self.wayland {
            dpe_wayland::dpe_wl_cursor_destroy(handle as *mut _);
        }
    }
}
impl Cursor {
    pub fn new() -> Self {
        let mut cursor = Self::default();
        if let Some(id) = std::env::var_os("DPE_GAME_CURSOR_TOPLEVEL")
            .and_then(|s| CString::new(s.as_encoded_bytes()).ok())
        {
            // SAFETY: terminated identifier is borrowed only during creation; native cursor stores its own validated string.
            let handle = unsafe { dpe_wayland::dpe_wl_cursor_create(id.as_ptr()) } as usize;
            cursor.wayland = (handle != 0).then_some(handle);
        }
        cursor
    }
    pub fn image(
        &mut self,
        window: u32,
        width: u32,
        height: u32,
    ) -> io::Result<Option<(Image, BorrowedFd<'_>)>> {
        let shape = &mut self.shape;
        let mut consume = |words: &[u32]| -> io::Result<Option<Image>> {
            if words.len() < 12 || words[0] == 0 || words[6] == 0 {
                return Ok(None);
            }
            let (serial, w, h) = (words[0], words[8], words[9]);
            if words[6] != 28
                || words[7] != 12
                || w == 0
                || h == 0
                || w > 256
                || h > 256
                || words[10] != w * 4
                || words[11] != 20
                || words.len() != 12 + (w * h) as usize
                || words[4] >= w
                || words[5] >= h
            {
                return Err(io::ErrorKind::InvalidData.into());
            }
            let x = (words[2] as i32)
                .checked_sub(words[4] as i32)
                .ok_or(io::ErrorKind::InvalidData)?;
            let y = (words[3] as i32)
                .checked_sub(words[5] as i32)
                .ok_or(io::ErrorKind::InvalidData)?;
            if !shape
                .as_ref()
                .is_some_and(|s| (s.serial, s.width, s.height) == (serial, w, h))
            {
                *shape = Some(Shape {
                    serial,
                    width: w,
                    height: h,
                    fd: system::sealed_bitmap(&words[12..])?,
                });
            }
            Ok(Some(Image {
                width: w,
                height: h,
                format: u32::from_le_bytes(*b"AR24"),
                planes: 1,
                storage: direct::CURSOR_BITMAP,
                stride: [w * 4, 0, 0, 0],
                source: Rect {
                    x: 0,
                    y: 0,
                    width: w,
                    height: h,
                },
                destination: Rect {
                    x,
                    y,
                    width: w,
                    height: h,
                },
                ..Default::default()
            }))
        };
        let image = if window != 0 {
            let handle = *self
                .x11
                .get_or_insert_with(|| dpe_x11::dpe_x11_cursor_create() as usize);
            dpe_x11::with_metadata(handle, window, width, height, &mut consume)
        } else {
            self.wayland
                .and_then(|h| dpe_wayland::with_metadata(h, width, height, &mut consume))
        }
        .transpose()?
        .flatten();
        Ok(image.and_then(|image| self.shape.as_ref().map(|shape| (image, shape.fd.as_fd()))))
    }
}
