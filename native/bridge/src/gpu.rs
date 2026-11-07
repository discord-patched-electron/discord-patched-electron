// SPDX-License-Identifier: MIT
//! Thread-confined VAAPI resources. No capture pixels are mapped before scaling.
use crate::ffi as va;
use std::{
    fs::OpenOptions,
    os::fd::{AsRawFd, BorrowedFd, OwnedFd},
    os::unix::fs::MetadataExt,
    ptr,
    rc::Rc,
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, &'static str>;
fn checked(status: i32) -> Result<()> {
    if status == 0 {
        Ok(())
    } else {
        Err("VAAPI operation failed")
    }
}
struct Display {
    handle: va::VADisplay,
    _fd: OwnedFd,
}
impl Drop for Display {
    fn drop(&mut self) {
        // SAFETY: the last resource owner terminates its initialized display.
        unsafe {
            va::vaTerminate(self.handle);
        }
    }
}
#[derive(Clone, Copy)]
enum Kind {
    Config,
    Context,
    Surface,
    Buffer,
    Image,
}
struct Resource {
    display: Rc<Display>,
    id: u32,
    kind: Kind,
    _fd: Option<OwnedFd>,
}
impl Drop for Resource {
    fn drop(&mut self) {
        // SAFETY: each successful VA allocation has one Resource owner, and Rc retains the display.
        unsafe {
            match self.kind {
                Kind::Config => {
                    va::vaDestroyConfig(self.display.handle, self.id);
                }
                Kind::Context => {
                    va::vaDestroyContext(self.display.handle, self.id);
                }
                Kind::Surface => {
                    va::vaDestroySurfaces(self.display.handle, &mut self.id, 1);
                }
                Kind::Buffer => {
                    va::vaDestroyBuffer(self.display.handle, self.id);
                }
                Kind::Image => {
                    va::vaDestroyImage(self.display.handle, self.id);
                }
            }
        }
    }
}
#[derive(PartialEq, Eq)]
struct Key {
    device: u64,
    inode: u64,
    width: u32,
    height: u32,
    size: u32,
    offset: [u32; 2],
    stride: [u32; 2],
    modifier: u64,
}
struct Imported {
    key: Key,
    surface: Resource,
}
struct Mapping {
    display: Rc<Display>,
    buffer: u32,
    pointer: *mut std::ffi::c_void,
}
impl Mapping {
    fn new(display: &Rc<Display>, buffer: u32) -> Result<Self> {
        let mut pointer = ptr::null_mut();
        // SAFETY: this owned VA buffer remains alive through the Mapping's scope.
        checked(unsafe { va::vaMapBuffer(display.handle, buffer, &mut pointer) })?;
        if pointer.is_null() {
            // A successful map still needs a matching unmap.
            // SAFETY: the map succeeded for this live display/buffer.
            unsafe {
                va::vaUnmapBuffer(display.handle, buffer);
            }
            return Err("null VA mapping");
        }
        Ok(Self {
            display: display.clone(),
            buffer,
            pointer,
        })
    }
}
impl Drop for Mapping {
    fn drop(&mut self) {
        // SAFETY: Mapping owns one successful mapping and is never cloned.
        unsafe {
            va::vaUnmapBuffer(self.display.handle, self.buffer);
        }
    }
}

/// A bounded thumbnail converter, constructed and used on its worker thread only.
/// Resource declaration order ensures surfaces/buffers precede context teardown.
pub struct Thumbnail {
    inputs: Vec<Imported>,
    output: Resource,
    image_owner: Resource,
    parameter: Resource,
    _context: Resource,
    _config: Resource,
    display: Rc<Display>,
    image: va::VAImage,
    context_id: u32,
    width: u32,
    height: u32,
    pending: bool,
}
impl Thumbnail {
    pub fn new(node: &std::ffi::CStr, width: u32, height: u32) -> Result<Self> {
        use std::os::unix::ffi::OsStrExt;
        if width == 0 || height == 0 || width > 8192 || height > 4320 || (width | height) & 1 != 0 {
            return Err("invalid preview dimensions");
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(std::ffi::OsStr::from_bytes(node.to_bytes()))
            .map_err(|_| "open render node")?;
        // SAFETY: the DRM descriptor stays open in Display for the entire VA display lifetime.
        let handle = unsafe { va::vaGetDisplayDRM(file.as_raw_fd()) };
        if handle.is_null() {
            return Err("no VA display");
        }
        let (mut major, mut minor) = (0, 0);
        // SAFETY: handle is from vaGetDisplayDRM, and outputs are valid integers.
        checked(unsafe { va::vaInitialize(handle, &mut major, &mut minor) })?;
        let display = Rc::new(Display {
            handle,
            _fd: file.into(),
        });
        let (mut config_id, mut context_id, mut surface_id, mut buffer_id) = (
            va::VA_INVALID_ID,
            va::VA_INVALID_ID,
            va::VA_INVALID_ID,
            va::VA_INVALID_ID,
        );
        // SAFETY: VA owns no attribute memory after this synchronous configuration call.
        checked(unsafe {
            va::vaCreateConfig(
                handle,
                va::VAProfile_VAProfileNone,
                va::VAEntrypoint_VAEntrypointVideoProc,
                ptr::null_mut(),
                0,
                &mut config_id,
            )
        })?;
        let config = Resource {
            display: display.clone(),
            id: config_id,
            kind: Kind::Config,
            _fd: None,
        };
        // SAFETY: dimensions are bounded; context targets are explicitly empty.
        checked(unsafe {
            va::vaCreateContext(
                handle,
                config_id,
                width as i32,
                height as i32,
                va::VA_PROGRESSIVE as i32,
                ptr::null_mut(),
                0,
                &mut context_id,
            )
        })?;
        let context = Resource {
            display: display.clone(),
            id: context_id,
            kind: Kind::Context,
            _fd: None,
        };
        let mut attribute = va::VASurfaceAttrib {
            type_: va::VASurfaceAttribType_VASurfaceAttribPixelFormat,
            flags: va::VA_SURFACE_ATTRIB_SETTABLE,
            ..Default::default()
        };
        attribute.value.type_ = va::VAGenericValueType_VAGenericValueTypeInteger;
        attribute.value.value.i = va::VA_FOURCC_NV12 as i32;
        // SAFETY: initialized attribute storage and surface output remain valid for the call.
        checked(unsafe {
            va::vaCreateSurfaces(
                handle,
                va::VA_RT_FORMAT_YUV420,
                width,
                height,
                &mut surface_id,
                1,
                &mut attribute,
                1,
            )
        })?;
        let output = Resource {
            display: display.clone(),
            id: surface_id,
            kind: Kind::Surface,
            _fd: None,
        };
        // SAFETY: querying limits has no borrowed output pointers.
        let count = unsafe { va::vaMaxNumImageFormats(handle) };
        if !(1..=256).contains(&count) {
            return Err("invalid image format count");
        }
        let mut formats = vec![va::VAImageFormat::default(); count as usize];
        let mut actual = count;
        // SAFETY: formats has space for the queried maximum.
        checked(unsafe { va::vaQueryImageFormats(handle, formats.as_mut_ptr(), &mut actual) })?;
        if actual < 0 || actual > count {
            return Err("image format overflow");
        }
        let mut format = formats
            .into_iter()
            .take(actual as usize)
            .find(|f| f.fourcc == va::VA_FOURCC_NV12)
            .ok_or("NV12 image unsupported")?;
        let mut image = va::VAImage::default();
        // SAFETY: bounded dimensions and initialized format/image outputs.
        checked(unsafe {
            va::vaCreateImage(handle, &mut format, width as i32, height as i32, &mut image)
        })?;
        let image_owner = Resource {
            display: display.clone(),
            id: image.image_id,
            kind: Kind::Image,
            _fd: None,
        };
        let mut parameters = va::VAProcPipelineParameterBuffer::default();
        // SAFETY: the initialized parameter bytes are copied by VA into its buffer.
        checked(unsafe {
            va::vaCreateBuffer(
                handle,
                context_id,
                va::VABufferType_VAProcPipelineParameterBufferType,
                std::mem::size_of_val(&parameters) as u32,
                1,
                (&raw mut parameters).cast(),
                &mut buffer_id,
            )
        })?;
        let parameter = Resource {
            display: display.clone(),
            id: buffer_id,
            kind: Kind::Buffer,
            _fd: None,
        };
        Ok(Self {
            inputs: Vec::new(),
            output,
            image_owner,
            parameter,
            _context: context,
            _config: config,
            display,
            image,
            context_id,
            width,
            height,
            pending: false,
        })
    }
    pub fn pending(&self) -> bool {
        self.pending
    }
    pub fn size(&self) -> (u32, u32) {
        (self.width, self.height)
    }
    fn import(&mut self, input: &va::encoder_nv12) -> Result<u32> {
        if input.fd < 0
            || input.width < 2
            || input.height < 2
            || input.width > 8192
            || input.height > 8192
        {
            return Err("invalid preview source");
        }
        for p in 0..2 {
            let rows = if p == 0 {
                input.height
            } else {
                input.height / 2
            };
            if input.stride[p] < input.width
                || u64::from(input.offset[p])
                    + u64::from(rows - 1) * u64::from(input.stride[p])
                    + u64::from(input.width)
                    > u64::from(input.size)
            {
                return Err("invalid NV12 extent");
            }
        }
        // SAFETY: input fd is borrowed under an independent capture allocation lease.
        let fd = unsafe { BorrowedFd::borrow_raw(input.fd) }
            .try_clone_to_owned()
            .map_err(|_| "duplicate NV12 descriptor")?;
        let file = std::fs::File::from(fd);
        let metadata = file.metadata().map_err(|_| "NV12 identity")?;
        let key = Key {
            device: metadata.dev(),
            inode: metadata.ino(),
            width: input.width,
            height: input.height,
            size: input.size,
            offset: input.offset,
            stride: input.stride,
            modifier: input.modifier,
        };
        if let Some(entry) = self.inputs.iter().find(|e| e.key == key) {
            return Ok(entry.surface.id);
        }
        let mut descriptor = va::VADRMPRIMESurfaceDescriptor {
            fourcc: va::VA_FOURCC_NV12,
            width: input.width,
            height: input.height,
            num_objects: 1,
            num_layers: 1,
            ..Default::default()
        };
        descriptor.objects[0].fd = file.as_raw_fd();
        descriptor.objects[0].size = input.size;
        descriptor.objects[0].drm_format_modifier = input.modifier;
        descriptor.layers[0].drm_format = va::VA_FOURCC_NV12;
        descriptor.layers[0].num_planes = 2;
        descriptor.layers[0].offset[..2].copy_from_slice(&input.offset);
        descriptor.layers[0].pitch[..2].copy_from_slice(&input.stride);
        let mut attributes = [va::VASurfaceAttrib::default(); 2];
        attributes[0].type_ = va::VASurfaceAttribType_VASurfaceAttribMemoryType;
        attributes[0].flags = va::VA_SURFACE_ATTRIB_SETTABLE;
        attributes[0].value.type_ = va::VAGenericValueType_VAGenericValueTypeInteger;
        attributes[0].value.value.i = va::VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2 as i32;
        attributes[1].type_ = va::VASurfaceAttribType_VASurfaceAttribExternalBufferDescriptor;
        attributes[1].flags = va::VA_SURFACE_ATTRIB_SETTABLE;
        attributes[1].value.type_ = va::VAGenericValueType_VAGenericValueTypePointer;
        attributes[1].value.value.p = (&raw mut descriptor).cast();
        let mut id = va::VA_INVALID_ID;
        // SAFETY: VA copies the bounded DMA-BUF layout; the Resource retains a duplicate fd.
        checked(unsafe {
            va::vaCreateSurfaces(
                self.display.handle,
                va::VA_RT_FORMAT_YUV420,
                input.width,
                input.height,
                &mut id,
                1,
                attributes.as_mut_ptr(),
                2,
            )
        })?;
        if self.inputs.len() == 4 {
            self.inputs.remove(0);
        }
        self.inputs.push(Imported {
            key,
            surface: Resource {
                display: self.display.clone(),
                id,
                kind: Kind::Surface,
                _fd: Some(file.into()),
            },
        });
        Ok(id)
    }
    pub fn convert(&mut self, input: &va::encoder_nv12, pixels: &mut Vec<u8>) -> Result<()> {
        let input = {
            let _profile = dpe_os::profiler::Span::new(21);
            self.import(input)?
        };
        let display = self.display.handle;
        {
            let _profile = dpe_os::profiler::Span::new(22);
            {
                let mapping = Mapping::new(&self.display, self.parameter.id)?;
                let parameters = va::VAProcPipelineParameterBuffer {
                    surface: input,
                    surface_color_standard: va::_VAProcColorStandardType_VAProcColorStandardBT709,
                    output_color_standard: va::_VAProcColorStandardType_VAProcColorStandardBT709,
                    ..Default::default()
                };
                // SAFETY: parameter was allocated at exactly this type's size and is exclusively mapped.
                unsafe {
                    mapping
                        .pointer
                        .cast::<va::VAProcPipelineParameterBuffer>()
                        .write(parameters);
                }
            }
            // SAFETY: context/output are owned, parameter is initialized and unmapped.
            checked(unsafe { va::vaBeginPicture(display, self.context_id, self.output.id) })?;
            self.pending = true;
            let rendered = {
                let mut buffer = self.parameter.id;
                // SAFETY: one live VA parameter buffer belongs to this context.
                unsafe { va::vaRenderPicture(display, self.context_id, &mut buffer, 1) }
            };
            // SAFETY: pair every successful BeginPicture, including RenderPicture failure.
            let ended = unsafe { va::vaEndPicture(display, self.context_id) };
            checked(rendered)?;
            checked(ended)?;
        }
        {
            let _profile = dpe_os::profiler::Span::new(23);
            let deadline = Instant::now() + Duration::from_millis(500);
            loop {
                let mut status = 0;
                // SAFETY: this owned output surface stays alive until the worker finishes reading it.
                checked(unsafe { va::vaQuerySurfaceStatus(display, self.output.id, &mut status) })?;
                if status == va::VASurfaceStatus_VASurfaceReady {
                    self.pending = false;
                    break;
                }
                if Instant::now() >= deadline {
                    return Err("preview GPU completion timeout");
                }
                std::thread::sleep(Duration::from_micros(500));
            }
        }
        let _profile = dpe_os::profiler::Span::new(24);
        // SAFETY: GPU completion precedes the readback, and image dimensions match the tiny output.
        checked(unsafe {
            va::vaGetImage(
                display,
                self.output.id,
                0,
                0,
                self.width,
                self.height,
                self.image_owner.id,
            )
        })?;
        let mapping = Mapping::new(&self.display, self.image.buf)?;
        let image = &self.image;
        let w = self.width as usize;
        let h = self.height as usize;
        if image.num_planes != 2 {
            return Err("preview image layout");
        }
        for p in 0..2 {
            let rows = if p == 0 { h } else { h / 2 };
            let stride = image.pitches[p] as usize;
            let offset = image.offsets[p] as usize;
            if stride < w || offset + (rows - 1) * stride + w > image.data_size as usize {
                return Err("preview image extent");
            }
        }
        // SAFETY: VA supplies image.data_size mapped bytes; every indexed row was bounds-checked above.
        let source = unsafe {
            std::slice::from_raw_parts(mapping.pointer.cast::<u8>(), image.data_size as usize)
        };
        pixels.resize(w * h * 3 / 2, 0);
        for row in 0..h {
            let start = image.offsets[0] as usize + row * image.pitches[0] as usize;
            pixels[row * w..(row + 1) * w].copy_from_slice(&source[start..start + w]);
        }
        for row in 0..h / 2 {
            let start = image.offsets[1] as usize + row * image.pitches[1] as usize;
            for col in 0..w / 2 {
                pixels[w * h + row * w / 2 + col] = source[start + col * 2];
                pixels[w * h * 5 / 4 + row * w / 2 + col] = source[start + col * 2 + 1];
            }
        }
        Ok(())
    }
}
