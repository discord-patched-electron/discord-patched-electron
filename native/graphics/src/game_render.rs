// SPDX-License-Identifier: GPL-3.0-only
//! GPU-only game copy and bounded thumbnail rendering. Cursor pixel borrows
//! remain inside their Rust owner callbacks; only a changed shape is uploaded.
use crate::{
    context::{Context, Image, Texture},
    ffi as f,
    target::Shader,
};
use dpe_core::cursor::Cursor;
use std::{
    ffi::{CStr, CString},
    os::fd::{FromRawFd, OwnedFd},
    ptr,
    rc::Rc,
};

pub fn device_uuid(context: &Context) -> Option<[u8; 16]> {
    if !context.current() {
        return None;
    }
    let mut uuid = [0u8; 16];
    // SAFETY: current retained context, exact extension ABI, fixed UUID output.
    unsafe {
        let extensions = f::glGetString(f::GL_EXTENSIONS);
        if extensions.is_null()
            || !CStr::from_ptr(extensions.cast())
                .to_bytes()
                .split(|b| *b == b' ')
                .any(|name| name == b"GL_EXT_memory_object")
        {
            return None;
        }
        let pointer = f::eglGetProcAddress(c"glGetUnsignedBytei_vEXT".as_ptr())?;
        let get = std::mem::transmute::<
            unsafe extern "C" fn(),
            unsafe extern "C" fn(u32, u32, *mut u8),
        >(pointer);
        get(0x9597, 0, uuid.as_mut_ptr());
        (f::glGetError() == f::GL_NO_ERROR).then_some(uuid)
    }
}

pub struct Input {
    pub texture: Texture,
    _image: Image,
}
impl Input {
    pub fn new(context: &Rc<Context>, packet: &f::dpe_vk_packet, fds: &[i32; 4]) -> Option<Self> {
        if !(1..=8192).contains(&packet.width)
            || !(1..=8192).contains(&packet.height)
            || !(1..=4).contains(&packet.nfd)
            || packet.flags & !f::DPE_VK_FLIP != 0
            || ![
                f::DPE_GRAPHICS_ARGB,
                f::DPE_GRAPHICS_ABGR,
                f::DPE_GRAPHICS_XRGB,
                f::DPE_GRAPHICS_XBGR,
            ]
            .contains(&packet.format)
        {
            return None;
        }
        let mut item = f::gsr_kms_response_item {
            width: packet.width,
            height: packet.height,
            pixel_format: packet.format,
            modifier: packet.modifier,
            num_dma_bufs: packet.nfd as i32,
            ..Default::default()
        };
        for (i, &fd) in fds.iter().enumerate().take(packet.nfd as usize) {
            if fd < 0
                || packet.strides[i] == 0
                || packet.strides[i] > 131072
                || packet.offsets[i] > i32::MAX as u32
            {
                return None;
            }
            item.dma_buf[i].fd = fd;
            item.dma_buf[i].pitch = packet.strides[i];
            item.dma_buf[i].offset = packet.offsets[i];
        }
        let image = context.import(&item)?;
        let texture = Texture::imported(&image)?;
        Some(Self {
            texture,
            _image: image,
        })
    }
}
pub struct Renderer {
    pub context: Rc<Context>,
    program: u32,
    flip: i32,
    cursor: i32,
    cursor_texture: u32,
    cursor_shape: Option<(u32, u32, u32)>,
}
impl Renderer {
    pub fn new(context: &Rc<Context>) -> Option<Self> {
        if !context.current() {
            return None;
        }
        let vertex = Shader::new(context, f::GL_VERTEX_SHADER, c"#version 300 es\nout vec2 uv; void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);uv=p;gl_Position=vec4(p*2.0-1.0,0.0,1.0);}")?;
        let fragment = Shader::new(context, f::GL_FRAGMENT_SHADER, c"#version 300 es\nprecision highp float;uniform sampler2D image;uniform bool flip;uniform bool cursor;in vec2 uv;out vec4 color;void main(){vec4 t=texture(image,vec2(uv.x,flip?1.0-uv.y:uv.y));color=cursor?t.bgra:vec4(t.rgb,1.0);}")?;
        // SAFETY: retained current context and owned shaders; local RAII owner
        // releases a partially linked program on every failure.
        unsafe {
            let mut result = Self {
                context: context.clone(),
                program: f::glCreateProgram(),
                flip: -1,
                cursor: -1,
                cursor_texture: 0,
                cursor_shape: None,
            };
            if result.program == 0 {
                return None;
            }
            f::glAttachShader(result.program, vertex.name);
            f::glAttachShader(result.program, fragment.name);
            f::glLinkProgram(result.program);
            let mut linked = 0;
            f::glGetProgramiv(result.program, f::GL_LINK_STATUS, &mut linked);
            result.flip = f::glGetUniformLocation(result.program, c"flip".as_ptr());
            result.cursor = f::glGetUniformLocation(result.program, c"cursor".as_ptr());
            (linked != 0
                && result.flip >= 0
                && result.cursor >= 0
                && f::glGetError() == f::GL_NO_ERROR)
                .then_some(result)
        }
    }
    pub fn draw(&self, input: &Input, flip: bool, size: (u32, u32)) -> bool {
        if !self.context.current() {
            return false;
        }
        // SAFETY: current program, live imported texture and bounded viewport.
        unsafe {
            f::glViewport(0, 0, size.0 as i32, size.1 as i32);
            f::glDisable(f::GL_DITHER);
            f::glDisable(f::GL_BLEND);
            f::glUseProgram(self.program);
            f::glActiveTexture(f::GL_TEXTURE0);
            f::glBindTexture(f::GL_TEXTURE_2D, input.texture.name);
            f::glUniform1i(self.flip, i32::from(flip));
            f::glUniform1i(self.cursor, 0);
            f::glDrawArrays(f::GL_TRIANGLES, 0, 3);
            f::glGetError() == f::GL_NO_ERROR
        }
    }
    fn draw_cursor(&mut self, words: &[u32]) {
        let Some(cursor) = Cursor::parse(words) else {
            return;
        };
        // SAFETY: bounded exact cursor storage stays borrowed from its Rust
        // owner through the synchronous GL upload; renderer context is current.
        unsafe {
            if self.cursor_texture == 0 {
                f::glGenTextures(1, &mut self.cursor_texture);
            }
            f::glBindTexture(f::GL_TEXTURE_2D, self.cursor_texture);
            let shape = (cursor.serial, cursor.width, cursor.height);
            if self.cursor_shape != Some(shape) {
                f::glTexImage2D(
                    f::GL_TEXTURE_2D,
                    0,
                    f::GL_RGBA as i32,
                    cursor.width as i32,
                    cursor.height as i32,
                    0,
                    f::GL_RGBA,
                    f::GL_UNSIGNED_BYTE,
                    cursor.pixels.as_ptr().cast(),
                );
                for (key, value) in [
                    (f::GL_TEXTURE_MIN_FILTER, f::GL_LINEAR),
                    (f::GL_TEXTURE_MAG_FILTER, f::GL_LINEAR),
                    (f::GL_TEXTURE_WRAP_S, f::GL_CLAMP_TO_EDGE),
                    (f::GL_TEXTURE_WRAP_T, f::GL_CLAMP_TO_EDGE),
                ] {
                    f::glTexParameteri(f::GL_TEXTURE_2D, key, value as i32);
                }
                self.cursor_shape = Some(shape);
            }
            f::glViewport(
                cursor.x,
                cursor.y,
                cursor.width as i32,
                cursor.height as i32,
            );
            f::glUniform1i(self.flip, 0);
            f::glUniform1i(self.cursor, 1);
            f::glEnable(f::GL_BLEND);
            f::glBlendFunc(f::GL_ONE, f::GL_ONE_MINUS_SRC_ALPHA);
            f::glDrawArrays(f::GL_TRIANGLES, 0, 3);
            f::glDisable(f::GL_BLEND);
            f::glUniform1i(self.cursor, 0);
        }
    }
    pub fn fence(&self) -> Option<OwnedFd> {
        // SAFETY: current retained context; export acquires one new descriptor
        // for all prior GL commands. No wait or full-frame readback is added.
        unsafe {
            let sync = (self.context.extensions.create_sync)(
                self.context.display,
                f::EGL_SYNC_NATIVE_FENCE_ANDROID,
                ptr::null(),
            );
            if sync.is_null() {
                return None;
            }
            f::glFlush();
            let fd = (self.context.extensions.dup_fence)(self.context.display, sync);
            (self.context.extensions.destroy_sync)(self.context.display, sync);
            (fd >= 0).then(|| OwnedFd::from_raw_fd(fd))
        }
    }
}
impl Drop for Renderer {
    fn drop(&mut self) {
        if self.context.current() {
            // SAFETY: renderer uniquely owns both names in its current context.
            unsafe {
                f::glDeleteTextures(1, &self.cursor_texture);
                if self.program != 0 {
                    f::glDeleteProgram(self.program);
                }
            }
        }
    }
}
pub struct CursorOwner {
    x11: usize,
    wayland: usize,
    is_wayland: bool,
}
impl CursorOwner {
    pub fn new() -> Self {
        let id = std::env::var_os("DPE_GAME_CURSOR_TOPLEVEL")
            .and_then(|s| CString::new(s.as_encoded_bytes()).ok());
        // SAFETY: terminated identifier remains borrowed only during creation.
        let wayland = id.map_or(
            0,
            |id| unsafe { dpe_wayland::dpe_wl_cursor_create(id.as_ptr()) } as usize,
        );
        // Wayland selection never falls back to a stale Xwayland cursor.
        Self {
            x11: 0,
            wayland,
            is_wayland: std::env::var_os("WAYLAND_DISPLAY").is_some(),
        }
    }
    pub fn draw(&mut self, renderer: &mut Renderer, window: u32, size: (u32, u32)) {
        if self.wayland != 0 {
            dpe_wayland::with_metadata(self.wayland, size.0, size.1, |words| {
                renderer.draw_cursor(words)
            });
        } else if !self.is_wayland && window != 0 {
            if self.x11 == 0 {
                self.x11 = dpe_x11::dpe_x11_cursor_create() as usize;
            }
            dpe_x11::with_metadata(self.x11, window, size.0, size.1, |words| {
                renderer.draw_cursor(words)
            });
        }
    }
}
impl Drop for CursorOwner {
    fn drop(&mut self) {
        dpe_x11::dpe_x11_cursor_destroy(self.x11 as *mut _);
        dpe_wayland::dpe_wl_cursor_destroy(self.wayland as *mut _);
    }
}
pub struct Thumbnail {
    context: Rc<Context>,
    texture: u32,
    framebuffer: u32,
    pub size: (u32, u32),
}
impl Thumbnail {
    pub fn new(context: &Rc<Context>, size: (u32, u32)) -> Option<Self> {
        if !(1..=480).contains(&size.0) || !(1..=270).contains(&size.1) || !context.current() {
            return None;
        }
        let mut result = Self {
            context: context.clone(),
            texture: 0,
            framebuffer: 0,
            size,
        };
        // SAFETY: current retained context; tiny bounded uninitialized GPU texture.
        unsafe {
            f::glGenTextures(1, &mut result.texture);
            f::glBindTexture(f::GL_TEXTURE_2D, result.texture);
            f::glTexImage2D(
                f::GL_TEXTURE_2D,
                0,
                f::GL_RGBA as i32,
                size.0 as i32,
                size.1 as i32,
                0,
                f::GL_RGBA,
                f::GL_UNSIGNED_BYTE,
                ptr::null(),
            );
            f::glTexParameteri(
                f::GL_TEXTURE_2D,
                f::GL_TEXTURE_MIN_FILTER,
                f::GL_LINEAR as i32,
            );
            f::glTexParameteri(
                f::GL_TEXTURE_2D,
                f::GL_TEXTURE_MAG_FILTER,
                f::GL_LINEAR as i32,
            );
            f::glGenFramebuffers(1, &mut result.framebuffer);
            f::glBindFramebuffer(f::GL_FRAMEBUFFER, result.framebuffer);
            f::glFramebufferTexture2D(
                f::GL_FRAMEBUFFER,
                f::GL_COLOR_ATTACHMENT0,
                f::GL_TEXTURE_2D,
                result.texture,
                0,
            );
            (result.texture != 0
                && result.framebuffer != 0
                && f::glCheckFramebufferStatus(f::GL_FRAMEBUFFER) == f::GL_FRAMEBUFFER_COMPLETE
                && f::glGetError() == f::GL_NO_ERROR)
                .then_some(result)
        }
    }
    pub fn bind(&self) {
        // SAFETY: thumbnail/context retained on this thread through rendering.
        unsafe {
            f::glBindFramebuffer(f::GL_FRAMEBUFFER, self.framebuffer);
        }
    }
    pub fn read(&self, pixels: &mut [u8]) -> bool {
        if pixels.len() != self.size.0 as usize * self.size.1 as usize * 4 {
            return false;
        }
        // SAFETY: bounded exact writable output after GPU-only scale to this FBO.
        let okay = unsafe {
            f::glPixelStorei(f::GL_PACK_ALIGNMENT, 1);
            f::glReadPixels(
                0,
                0,
                self.size.0 as i32,
                self.size.1 as i32,
                f::GL_RGBA,
                f::GL_UNSIGNED_BYTE,
                pixels.as_mut_ptr().cast(),
            );
            f::glGetError() == f::GL_NO_ERROR
        };
        if okay {
            for pixel in pixels.as_chunks_mut::<4>().0 {
                pixel.swap(0, 2);
                pixel[3] = 255;
            }
        }
        okay
    }
}
impl Drop for Thumbnail {
    fn drop(&mut self) {
        if self.context.current() {
            // SAFETY: unique names in a retained current context.
            unsafe {
                f::glDeleteFramebuffers(1, &self.framebuffer);
                f::glDeleteTextures(1, &self.texture);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "requires an explicitly selected render node; owns synthetic pixels only"]
    fn synthetic_cursor_sprite_alpha_clipping_and_hidden_state() {
        let node =
            CString::new(std::env::var("DPE_GRAPHICS_TEST_NODE").expect("explicit render node"))
                .unwrap();
        let context = Context::open(&node).unwrap();
        let mut renderer = Renderer::new(&context).unwrap();
        let target = Thumbnail::new(&context, (16, 16)).unwrap();
        let program = renderer.program;
        let clear = || {
            target.bind();
            // SAFETY: owned synthetic FBO and program in the retained context.
            unsafe {
                f::glViewport(0, 0, 16, 16);
                f::glClearColor(0.0, 0.0, 1.0, 1.0);
                f::glClear(f::GL_COLOR_BUFFER_BIT);
                f::glUseProgram(program);
            }
        };
        let mut words = [
            7u32, 0, 5, 6, 1, 1, 28, 12, 2, 2, 8, 20, 0xffff0000, 0xffff0000, 0xff00ff00,
            0xff00ff00,
        ];
        let mut pixels = [0u8; 16 * 16 * 4];
        clear();
        renderer.draw_cursor(&words);
        assert!(target.read(&mut pixels));
        for y in 0..16 {
            for x in 0..16 {
                let offset = (y * 16 + x) * 4;
                let red = (4..6).contains(&x) && y == 5;
                let green = (4..6).contains(&x) && y == 6;
                assert_eq!(
                    &pixels[offset..offset + 3],
                    &[
                        if red || green { 0 } else { 255 },
                        if green { 255 } else { 0 },
                        if red { 255 } else { 0 }
                    ]
                );
            }
        }
        words[0] += 1;
        words[12..].fill(0x80800000);
        clear();
        renderer.draw_cursor(&words);
        assert!(target.read(&mut pixels));
        let offset = (5 * 16 + 4) * 4;
        assert_eq!(&pixels[offset..offset + 3], &[127, 0, 128]);
        words[2] = 0;
        words[3] = 0;
        clear();
        renderer.draw_cursor(&words);
        assert!(target.read(&mut pixels));
        assert_eq!(&pixels[..3], &[127, 0, 128]);
        assert_eq!(&pixels[4..7], &[255, 0, 0]);
        words[0] = 0;
        clear();
        renderer.draw_cursor(&words);
        assert!(target.read(&mut pixels));
        assert!(
            pixels
                .as_chunks::<4>()
                .0
                .iter()
                .all(|p| p[..3] == [255, 0, 0])
        );
    }
}
