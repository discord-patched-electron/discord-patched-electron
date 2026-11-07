// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    context::{Context, Image, Texture},
    ffi as f,
};
use std::{
    cell::OnceCell,
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    ptr,
    rc::Rc,
};

struct Allocation {
    _context: Rc<Context>,
    handle: *mut f::gbm_bo,
}
impl Drop for Allocation {
    fn drop(&mut self) {
        // SAFETY: unique GBM allocation, retaining its underlying device.
        unsafe {
            f::gbm_bo_destroy(self.handle);
        }
    }
}
struct Framebuffer {
    context: Rc<Context>,
    name: u32,
}
impl Drop for Framebuffer {
    fn drop(&mut self) {
        if self.name != 0 && self.context.current() {
            // SAFETY: unique framebuffer in the retained current context.
            unsafe {
                f::glDeleteFramebuffers(1, &self.name);
            }
        }
    }
}
pub struct Target {
    // Destruction order: GL references, imported image, export, backing storage.
    framebuffer: Framebuffer,
    _texture: Texture,
    _image: Image,
    pub export: OwnedFd,
    _allocation: Allocation,
    pub width: u32,
    pub height: u32,
    pub stride: u32,
}
impl Target {
    pub fn new(context: &Rc<Context>, width: u32, height: u32) -> Option<Self> {
        if !(1..=8192).contains(&width) || !(1..=8192).contains(&height) || !context.current() {
            return None;
        }
        let modifier = 0u64;
        // SAFETY: retained GBM device, bounded dimensions and one live modifier.
        let handle = unsafe {
            f::gbm_bo_create_with_modifiers2(
                context.gbm,
                width,
                height,
                f::DPE_GRAPHICS_ARGB,
                &modifier,
                1,
                f::DPE_GRAPHICS_RENDERING,
            )
        };
        if handle.is_null() {
            return None;
        }
        let allocation = Allocation {
            _context: context.clone(),
            handle,
        };
        // SAFETY: allocation uniquely owns this live GBM BO through all queries.
        let (actual_modifier, planes, offset, fd, stride) = unsafe {
            (
                f::gbm_bo_get_modifier(handle),
                f::gbm_bo_get_plane_count(handle),
                f::gbm_bo_get_offset(handle, 0),
                f::gbm_bo_get_fd_for_plane(handle, 0),
                f::gbm_bo_get_stride_for_plane(handle, 0),
            )
        };
        if fd < 0 {
            return None;
        }
        // SAFETY: gbm exported a new descriptor, adopted before further validation.
        let export = unsafe { OwnedFd::from_raw_fd(fd) };
        if actual_modifier != modifier
            || planes != 1
            || offset != 0
            || stride < width * 4
            || stride.checked_mul(height).is_none()
        {
            return None;
        }
        let mut item = f::gsr_kms_response_item {
            width,
            height,
            pixel_format: f::DPE_GRAPHICS_ARGB,
            modifier,
            num_dma_bufs: 1,
            ..Default::default()
        };
        item.dma_buf[0].fd = export.as_raw_fd();
        item.dma_buf[0].pitch = stride;
        let image = context.import(&item)?;
        let texture = Texture::imported(&image)?;
        let mut framebuffer = Framebuffer {
            context: context.clone(),
            name: 0,
        };
        // SAFETY: current context and live owned GL texture/output framebuffer.
        let ready = unsafe {
            f::glGenFramebuffers(1, &mut framebuffer.name);
            f::glBindFramebuffer(f::GL_FRAMEBUFFER, framebuffer.name);
            f::glFramebufferTexture2D(
                f::GL_FRAMEBUFFER,
                f::GL_COLOR_ATTACHMENT0,
                f::GL_TEXTURE_2D,
                texture.name,
                0,
            );
            framebuffer.name != 0
                && f::glCheckFramebufferStatus(f::GL_FRAMEBUFFER) == f::GL_FRAMEBUFFER_COMPLETE
                && f::glGetError() == f::GL_NO_ERROR
        };
        ready.then_some(Self {
            framebuffer,
            _texture: texture,
            _image: image,
            export,
            _allocation: allocation,
            width,
            height,
            stride,
        })
    }
    pub fn bind(&self) -> bool {
        if !self.framebuffer.context.current() {
            return false;
        }
        // SAFETY: retained context/framebuffer, bounded GL viewport dimensions.
        unsafe {
            f::glBindFramebuffer(f::GL_FRAMEBUFFER, self.framebuffer.name);
            f::glViewport(0, 0, self.width as i32, self.height as i32);
            f::glDisable(f::GL_DITHER);
            f::glActiveTexture(f::GL_TEXTURE0);
        }
        true
    }
    pub fn read_bgra(&self, pixels: &mut [u8]) -> bool {
        if pixels.len() != self.width as usize * self.height as usize * 4 || !self.bind() {
            return false;
        }
        // SAFETY: exactly sized writable pixel slice for this bounded framebuffer.
        let okay = unsafe {
            f::glReadPixels(
                0,
                0,
                self.width as i32,
                self.height as i32,
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

pub(crate) struct Shader {
    context: Rc<Context>,
    pub(crate) name: u32,
}
impl Shader {
    pub(crate) fn new(context: &Rc<Context>, kind: u32, source: &std::ffi::CStr) -> Option<Self> {
        // SAFETY: current context, NUL-terminated static shader and valid output.
        unsafe {
            let shader = Self {
                context: context.clone(),
                name: f::glCreateShader(kind),
            };
            if shader.name == 0 {
                return None;
            }
            f::glShaderSource(shader.name, 1, &source.as_ptr(), ptr::null());
            f::glCompileShader(shader.name);
            let mut status = 0;
            f::glGetShaderiv(shader.name, f::GL_COMPILE_STATUS, &mut status);
            (status != 0).then_some(shader)
        }
    }
}
impl Drop for Shader {
    fn drop(&mut self) {
        if self.name != 0 && self.context.current() {
            // SAFETY: unique shader in its live context.
            unsafe {
                f::glDeleteShader(self.name);
            }
        }
    }
}
pub struct Program {
    pub context: Rc<Context>,
    pub name: u32,
    source: i32,
    destination: i32,
    alpha: i32,
    input: Texture,
    cursor: OnceCell<Texture>,
}
impl Program {
    pub fn new(context: &Rc<Context>) -> Option<Self> {
        if !context.current() {
            return None;
        }
        let vertex = Shader::new(context, f::GL_VERTEX_SHADER, c"#version 300 es\nuniform vec4 sourceRect,destinationRect;out vec2 uv;void main(){vec2 p=vec2(gl_VertexID&1,gl_VertexID>>1);uv=sourceRect.xy+p*sourceRect.zw;gl_Position=vec4((destinationRect.xy+p*destinationRect.zw)*2.0-1.0,0,1);}")?;
        let fragment = Shader::new(context, f::GL_FRAGMENT_SHADER, c"#version 300 es\nprecision highp float;uniform sampler2D image;uniform bool useAlpha;in vec2 uv;out vec4 color;void main(){vec4 p=texture(image,uv);color=vec4(p.rgb,useAlpha?p.a:1.0);}")?;
        let input = Texture::new(context)?;
        // SAFETY: current context; owned shaders outlive link and all outputs.
        unsafe {
            let mut program = Self {
                context: context.clone(),
                name: f::glCreateProgram(),
                source: -1,
                destination: -1,
                alpha: -1,
                input,
                cursor: OnceCell::new(),
            };
            if program.name == 0 {
                return None;
            }
            f::glAttachShader(program.name, vertex.name);
            f::glAttachShader(program.name, fragment.name);
            f::glLinkProgram(program.name);
            let mut status = 0;
            f::glGetProgramiv(program.name, f::GL_LINK_STATUS, &mut status);
            program.source = f::glGetUniformLocation(program.name, c"sourceRect".as_ptr());
            program.destination =
                f::glGetUniformLocation(program.name, c"destinationRect".as_ptr());
            program.alpha = f::glGetUniformLocation(program.name, c"useAlpha".as_ptr());
            (status != 0
                && program.source >= 0
                && program.destination >= 0
                && program.alpha >= 0
                && f::glGetError() == f::GL_NO_ERROR)
                .then_some(program)
        }
    }
    pub fn draw(&self, item: &f::gsr_kms_response_item, cursor: bool, canvas: (u32, u32)) -> bool {
        use dpe_core::geometry::Rect;
        let source = Rect {
            x: item.src_x,
            y: item.src_y,
            width: item.src_w.max(0) as u32,
            height: item.src_h.max(0) as u32,
        };
        if !source.contained((item.width, item.height))
            || item.dst_w <= 0
            || item.dst_h <= 0
            || canvas.0 == 0
            || canvas.1 == 0
            || !self.context.current()
        {
            return false;
        }
        let Some(image) = self.context.import(item) else {
            return false;
        };
        let texture = if cursor {
            if self.cursor.get().is_none() {
                let Some(texture) = Texture::new(&self.context) else {
                    return false;
                };
                let _ = self.cursor.set(texture);
            }
            self.cursor.get().expect("initialized cursor texture")
        } else {
            &self.input
        };
        if !texture.bind_image(&image) {
            return false;
        }
        // SAFETY: current program and scoped image/texture; checked source rect
        // and nonzero denominators. The viewport clips partially visible cursors.
        unsafe {
            f::glUseProgram(self.name);
            f::glBindTexture(f::GL_TEXTURE_2D, texture.name);
            f::glUniform4f(
                self.source,
                item.src_x as f32 / item.width as f32,
                item.src_y as f32 / item.height as f32,
                item.src_w as f32 / item.width as f32,
                item.src_h as f32 / item.height as f32,
            );
            f::glUniform4f(
                self.destination,
                item.dst_x as f32 / canvas.0 as f32,
                item.dst_y as f32 / canvas.1 as f32,
                item.dst_w as f32 / canvas.0 as f32,
                item.dst_h as f32 / canvas.1 as f32,
            );
            f::glUniform1i(self.alpha, i32::from(cursor));
            if cursor {
                f::glEnable(f::GL_BLEND);
                f::glBlendFunc(f::GL_ONE, f::GL_ONE_MINUS_SRC_ALPHA);
            } else {
                f::glDisable(f::GL_BLEND);
            }
            f::glDrawArrays(f::GL_TRIANGLE_STRIP, 0, 4);
            f::glGetError() == f::GL_NO_ERROR
        }
    }
    pub fn fence(&self) -> Option<OwnedFd> {
        // SAFETY: current retained context, extension functions validated during
        // construction. Exported sync-file owns all preceding queued GL work.
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
impl Drop for Program {
    fn drop(&mut self) {
        if self.name != 0 && self.context.current() {
            // SAFETY: unique program in the retained current context.
            unsafe {
                f::glDeleteProgram(self.name);
            }
        }
    }
}
