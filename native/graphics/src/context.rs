// SPDX-License-Identifier: GPL-3.0-only
use crate::ffi as f;
use std::{
    ffi::{CStr, c_void},
    fs::OpenOptions,
    os::{
        fd::AsRawFd,
        unix::{ffi::OsStrExt, fs::OpenOptionsExt},
    },
    ptr,
    rc::Rc,
};

pub struct Extensions {
    pub create_image: unsafe extern "C" fn(
        f::EGLDisplay,
        f::EGLContext,
        f::EGLenum,
        f::EGLClientBuffer,
        *const f::EGLint,
    ) -> f::EGLImageKHR,
    pub destroy_image: unsafe extern "C" fn(f::EGLDisplay, f::EGLImageKHR) -> f::EGLBoolean,
    pub image_texture: unsafe extern "C" fn(f::GLenum, f::GLeglImageOES),
    pub create_sync:
        unsafe extern "C" fn(f::EGLDisplay, f::EGLenum, *const f::EGLint) -> f::EGLSyncKHR,
    pub destroy_sync: unsafe extern "C" fn(f::EGLDisplay, f::EGLSyncKHR) -> f::EGLBoolean,
    pub dup_fence: unsafe extern "C" fn(f::EGLDisplay, f::EGLSyncKHR) -> f::EGLint,
}
// No Send/Sync: GBM, EGL, GL and all users remain on the creating thread (Rc).
pub struct Context {
    pub file: std::fs::File,
    pub gbm: *mut f::gbm_device,
    pub display: f::EGLDisplay,
    handle: f::EGLContext,
    pub extensions: Extensions,
}
struct Setup {
    gbm: *mut f::gbm_device,
    display: f::EGLDisplay,
    context: f::EGLContext,
}
impl Drop for Setup {
    fn drop(&mut self) {
        // SAFETY: setup owns these optional resources; no child GL owners exist.
        unsafe {
            if !self.context.is_null() {
                f::eglMakeCurrent(
                    self.display,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                );
                f::eglDestroyContext(self.display, self.context);
            }
            if !self.display.is_null() {
                f::eglTerminate(self.display);
            }
            if !self.gbm.is_null() {
                f::gbm_device_destroy(self.gbm);
            }
        }
    }
}
fn extension(list: &[u8], name: &[u8]) -> bool {
    list.split(|byte| *byte == b' ').any(|item| item == name)
}
impl Context {
    pub fn open(node: &CStr) -> Option<Rc<Self>> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(std::path::Path::new(std::ffi::OsStr::from_bytes(
                node.to_bytes(),
            )))
            .ok()?;
        let mut setup = Setup {
            gbm: ptr::null_mut(),
            display: ptr::null_mut(),
            context: ptr::null_mut(),
        };
        // SAFETY: live owned device fd and initialized optional output handles;
        // every failure unwinds Setup before closing its underlying file.
        unsafe {
            setup.gbm = f::gbm_create_device(file.as_raw_fd());
            if setup.gbm.is_null() {
                return None;
            }
            setup.display =
                f::eglGetPlatformDisplay(f::EGL_PLATFORM_GBM_KHR, setup.gbm.cast(), ptr::null());
            if setup.display.is_null()
                || f::eglInitialize(setup.display, ptr::null_mut(), ptr::null_mut()) == 0
            {
                return None;
            }
            let extensions = f::eglQueryString(setup.display, f::EGL_EXTENSIONS as i32);
            if extensions.is_null() {
                return None;
            }
            let extensions = CStr::from_ptr(extensions).to_bytes();
            for required in [
                b"EGL_KHR_surfaceless_context".as_slice(),
                b"EGL_KHR_no_config_context",
                b"EGL_EXT_image_dma_buf_import_modifiers",
                b"EGL_ANDROID_native_fence_sync",
            ] {
                if !extension(extensions, required) {
                    return None;
                }
            }
            macro_rules! load {
                ($name:literal, $kind:ty) => {{
                    let pointer = f::eglGetProcAddress($name.as_ptr())?;
                    std::mem::transmute::<unsafe extern "C" fn(), $kind>(pointer)
                }};
            }
            let extensions = Extensions {
                create_image: load!(
                    c"eglCreateImageKHR",
                    unsafe extern "C" fn(
                        f::EGLDisplay,
                        f::EGLContext,
                        f::EGLenum,
                        f::EGLClientBuffer,
                        *const f::EGLint,
                    ) -> f::EGLImageKHR
                ),
                destroy_image: load!(
                    c"eglDestroyImageKHR",
                    unsafe extern "C" fn(f::EGLDisplay, f::EGLImageKHR) -> f::EGLBoolean
                ),
                image_texture: load!(
                    c"glEGLImageTargetTexture2DOES",
                    unsafe extern "C" fn(f::GLenum, f::GLeglImageOES)
                ),
                create_sync: load!(
                    c"eglCreateSyncKHR",
                    unsafe extern "C" fn(
                        f::EGLDisplay,
                        f::EGLenum,
                        *const f::EGLint,
                    ) -> f::EGLSyncKHR
                ),
                destroy_sync: load!(
                    c"eglDestroySyncKHR",
                    unsafe extern "C" fn(f::EGLDisplay, f::EGLSyncKHR) -> f::EGLBoolean
                ),
                dup_fence: load!(
                    c"eglDupNativeFenceFDANDROID",
                    unsafe extern "C" fn(f::EGLDisplay, f::EGLSyncKHR) -> f::EGLint
                ),
            };
            if f::eglBindAPI(f::EGL_OPENGL_ES_API) == 0 {
                return None;
            }
            let attributes = [f::EGL_CONTEXT_CLIENT_VERSION as i32, 3, f::EGL_NONE as i32];
            setup.context = f::eglCreateContext(
                setup.display,
                ptr::null_mut(),
                ptr::null_mut(),
                attributes.as_ptr(),
            );
            if setup.context.is_null()
                || f::eglMakeCurrent(
                    setup.display,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    setup.context,
                ) == 0
            {
                return None;
            }
            let result = Self {
                file,
                gbm: setup.gbm,
                display: setup.display,
                handle: setup.context,
                extensions,
            };
            setup.gbm = ptr::null_mut();
            setup.display = ptr::null_mut();
            setup.context = ptr::null_mut();
            Some(Rc::new(result))
        }
    }
    pub fn current(&self) -> bool {
        // SAFETY: this thread retains the display/context and never shares it.
        unsafe {
            (f::eglGetCurrentContext() == self.handle && f::eglGetCurrentDisplay() == self.display)
                || f::eglMakeCurrent(self.display, ptr::null_mut(), ptr::null_mut(), self.handle)
                    != 0
        }
    }
    /// Import a validated descriptor record only for the duration of this call.
    /// The caller retains its DMA-BUF FDs until EGL has acquired their storage.
    pub fn import(self: &Rc<Self>, item: &f::gsr_kms_response_item) -> Option<Image> {
        let attributes = image_attributes(item)?;
        // SAFETY: fixed terminated attribute array with validated 1..4 planes;
        // caller's live DMA-BUFs remain borrowed through this synchronous import.
        let image = unsafe {
            (self.extensions.create_image)(
                self.display,
                ptr::null_mut(),
                f::EGL_LINUX_DMA_BUF_EXT,
                ptr::null_mut(),
                attributes.as_ptr(),
            )
        };
        (!image.is_null()).then(|| Image {
            context: self.clone(),
            handle: image,
        })
    }
}
impl Drop for Context {
    fn drop(&mut self) {
        // SAFETY: last Rc; all dependent GL/images/BOs have already been dropped.
        unsafe {
            f::eglMakeCurrent(
                self.display,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
            );
            f::eglDestroyContext(self.display, self.handle);
            f::eglTerminate(self.display);
            f::gbm_device_destroy(self.gbm);
        }
    }
}
pub struct Image {
    pub context: Rc<Context>,
    pub handle: f::EGLImageKHR,
}
impl Drop for Image {
    fn drop(&mut self) {
        // SAFETY: unique EGL image; Rc retains the live display through deletion.
        unsafe {
            (self.context.extensions.destroy_image)(self.context.display, self.handle);
        }
    }
}
pub struct Texture {
    pub context: Rc<Context>,
    pub name: u32,
}
impl Texture {
    pub fn new(context: &Rc<Context>) -> Option<Self> {
        if !context.current() {
            return None;
        }
        let mut texture = Self {
            context: context.clone(),
            name: 0,
        };
        // SAFETY: current retained context and uniquely owned output texture.
        unsafe {
            f::glGenTextures(1, &mut texture.name);
            f::glBindTexture(f::GL_TEXTURE_2D, texture.name);
            for (key, value) in [
                (f::GL_TEXTURE_MIN_FILTER, f::GL_LINEAR),
                (f::GL_TEXTURE_MAG_FILTER, f::GL_LINEAR),
                (f::GL_TEXTURE_WRAP_S, f::GL_CLAMP_TO_EDGE),
                (f::GL_TEXTURE_WRAP_T, f::GL_CLAMP_TO_EDGE),
            ] {
                f::glTexParameteri(f::GL_TEXTURE_2D, key, value as i32);
            }
            (texture.name != 0 && f::glGetError() == f::GL_NO_ERROR).then_some(texture)
        }
    }
    pub fn bind_image(&self, image: &Image) -> bool {
        if !Rc::ptr_eq(&self.context, &image.context) || !self.context.current() {
            return false;
        }
        // SAFETY: both owners share this current EGL display/context. Rebinding
        // updates texture storage in command order; EGL/GL retain queued image
        // use after the scoped import is destroyed, as in GSR's capture path.
        unsafe {
            f::glBindTexture(f::GL_TEXTURE_2D, self.name);
            (self.context.extensions.image_texture)(
                f::GL_TEXTURE_2D,
                image.handle.cast::<c_void>(),
            );
            f::glGetError() == f::GL_NO_ERROR
        }
    }
    pub fn imported(image: &Image) -> Option<Self> {
        let texture = Self::new(&image.context)?;
        texture.bind_image(image).then_some(texture)
    }
}
impl Drop for Texture {
    fn drop(&mut self) {
        if self.name != 0 && self.context.current() {
            // SAFETY: unique texture name in the retained current context.
            unsafe {
                f::glDeleteTextures(1, &self.name);
            }
        }
    }
}
pub fn image_attributes(item: &f::gsr_kms_response_item) -> Option<[i32; 64]> {
    if !(1..=16384).contains(&item.width)
        || !(1..=16384).contains(&item.height)
        || !(1..=4).contains(&item.num_dma_bufs)
    {
        return None;
    }
    let mut result = [f::EGL_NONE as i32; 64];
    let mut count = 0;
    let mut push = |key: u32, value: u32| {
        result[count] = key as i32;
        result[count + 1] = value as i32;
        count += 2;
    };
    push(f::EGL_WIDTH, item.width);
    push(f::EGL_HEIGHT, item.height);
    push(f::EGL_LINUX_DRM_FOURCC_EXT, item.pixel_format);
    let keys = [
        f::EGL_DMA_BUF_PLANE0_FD_EXT,
        f::EGL_DMA_BUF_PLANE1_FD_EXT,
        f::EGL_DMA_BUF_PLANE2_FD_EXT,
        f::EGL_DMA_BUF_PLANE3_FD_EXT,
    ];
    let modifiers = [
        f::EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT,
        f::EGL_DMA_BUF_PLANE1_MODIFIER_LO_EXT,
        f::EGL_DMA_BUF_PLANE2_MODIFIER_LO_EXT,
        f::EGL_DMA_BUF_PLANE3_MODIFIER_LO_EXT,
    ];
    for index in 0..item.num_dma_bufs as usize {
        let plane = &item.dma_buf[index];
        if plane.fd < 0
            || plane.pitch == 0
            || plane.pitch > i32::MAX as u32
            || plane.offset > i32::MAX as u32
        {
            return None;
        }
        push(keys[index], plane.fd as u32);
        push(keys[index] + 1, plane.offset);
        push(keys[index] + 2, plane.pitch);
        if item.modifier != f::DPE_GRAPHICS_MOD_INVALID {
            push(modifiers[index], item.modifier as u32);
            push(modifiers[index] + 1, (item.modifier >> 32) as u32);
        }
    }
    Some(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_extension_tokens_and_bounded_plane_attributes() {
        assert!(extension(
            b"a EGL_KHR_no_config_context b",
            b"EGL_KHR_no_config_context"
        ));
        assert!(!extension(
            b"EGL_KHR_no_config_context2",
            b"EGL_KHR_no_config_context"
        ));
        let mut item = f::gsr_kms_response_item {
            width: 1920,
            height: 1080,
            num_dma_bufs: 4,
            modifier: 0x0123456789abcdef,
            ..Default::default()
        };
        for (i, plane) in item.dma_buf.iter_mut().enumerate() {
            plane.fd = i as i32;
            plane.pitch = 7680;
        }
        let attributes = image_attributes(&item).unwrap();
        assert_eq!(attributes[46], f::EGL_NONE as i32);
        assert_eq!(attributes[15] as u32, 0x01234567);
        item.num_dma_bufs = 5;
        assert!(image_attributes(&item).is_none());
        item.num_dma_bufs = 1;
        item.dma_buf[0].fd = -1;
        assert!(image_attributes(&item).is_none());
        item.dma_buf[0].fd = 1;
        item.dma_buf[0].offset = u32::MAX;
        assert!(image_attributes(&item).is_none());
    }
}
