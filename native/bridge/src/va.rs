// SPDX-License-Identifier: MIT
//! Owned VAAPI resources. Object dependencies retain their parents. Image maps
//! borrow the image, preventing destruction before unmapping. Submitters must
//! serialize each context; independent objects may use the thread-safe VA API.
use crate::ffi as va;
use std::{
    ffi::{CStr, c_void},
    fs::OpenOptions,
    os::{
        fd::{AsRawFd, OwnedFd},
        unix::{
            ffi::OsStrExt,
            fs::{MetadataExt, OpenOptionsExt},
        },
    },
    ptr::{self, NonNull},
    sync::Arc,
    time::{Duration, Instant},
};
pub type Result<T> = std::result::Result<T, &'static str>;
pub fn checked(status: i32) -> Result<()> {
    if status == 0 {
        Ok(())
    } else {
        Err("VAAPI operation failed")
    }
}
pub struct Display {
    handle: va::VADisplay,
    _fd: OwnedFd,
    sync: Option<unsafe extern "C" fn(va::VADisplay, u32, u64) -> i32>,
}
// SAFETY: libva's multithreading contract explicitly permits independent VA
// objects across threads. Display has no mutable Rust state and owns its fd.
// Context submission and surface reuse are serialized by their outer owners.
unsafe impl Send for Display {}
// SAFETY: same libva contract; an Arc only shares the immutable display handle.
unsafe impl Sync for Display {}
impl Display {
    pub fn handle(&self) -> va::VADisplay {
        self.handle
    }
    pub fn open(node: &CStr) -> Result<Arc<Self>> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(std::ffi::OsStr::from_bytes(node.to_bytes()))
            .map_err(|_| "open render node")?;
        if file.metadata().map_err(|_| "render node metadata")?.mode() & libc::S_IFMT
            != libc::S_IFCHR
        {
            return Err("render node is not a character device");
        }
        // SAFETY: fd remains owned until after the last dependent VA object.
        let handle = unsafe { va::vaGetDisplayDRM(file.as_raw_fd()) };
        if handle.is_null() {
            return Err("no VA display");
        }
        let (mut major, mut minor) = (0, 0);
        // SAFETY: returned display handle and local version outputs are valid.
        checked(unsafe { va::vaInitialize(handle, &mut major, &mut minor) })?;
        // SAFETY: optional public libva symbol; its ABI is fixed by va.h.
        let sync = unsafe {
            let pointer = libc::dlsym(libc::RTLD_DEFAULT, c"vaSyncSurface2".as_ptr());
            if pointer.is_null() {
                None
            } else {
                Some(std::mem::transmute::<
                    *mut c_void,
                    unsafe extern "C" fn(va::VADisplay, u32, u64) -> i32,
                >(pointer))
            }
        };
        Ok(Arc::new(Self {
            handle,
            _fd: file.into(),
            sync,
        }))
    }
}
impl Drop for Display {
    fn drop(&mut self) {
        // SAFETY: every dependent resource has released its Arc.
        unsafe {
            va::vaTerminate(self.handle);
        }
    }
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Config,
    Context,
    Surface,
    Buffer,
    Image,
}
pub struct Resource {
    display: Arc<Display>,
    id: u32,
    kind: Kind,
    _parent: Option<Arc<Resource>>,
    _fds: Vec<OwnedFd>,
}
impl Resource {
    pub fn id(&self) -> u32 {
        self.id
    }
    pub fn display(&self) -> &Arc<Display> {
        &self.display
    }
    fn new(
        display: &Arc<Display>,
        id: u32,
        kind: Kind,
        parent: Option<Arc<Resource>>,
        fds: Vec<OwnedFd>,
    ) -> Arc<Self> {
        Arc::new(Self {
            display: display.clone(),
            id,
            kind,
            _parent: parent,
            _fds: fds,
        })
    }
    pub fn config(display: &Arc<Display>) -> Result<Arc<Self>> {
        let mut id = va::VA_INVALID_ID;
        // SAFETY: initialized output and no optional attribute pointers.
        checked(unsafe {
            va::vaCreateConfig(
                display.handle,
                va::VAProfile_VAProfileNone,
                va::VAEntrypoint_VAEntrypointVideoProc,
                ptr::null_mut(),
                0,
                &mut id,
            )
        })?;
        Ok(Self::new(display, id, Kind::Config, None, Vec::new()))
    }
    pub fn context(config: &Arc<Self>, width: u32, height: u32) -> Result<Arc<Self>> {
        if config.kind != Kind::Config || width == 0 || height == 0 || width > 8192 || height > 8192
        {
            return Err("invalid VPP context");
        }
        let mut id = va::VA_INVALID_ID;
        // SAFETY: config is retained by the new context; geometry is bounded.
        checked(unsafe {
            va::vaCreateContext(
                config.display.handle,
                config.id,
                width as i32,
                height as i32,
                va::VA_PROGRESSIVE as i32,
                ptr::null_mut(),
                0,
                &mut id,
            )
        })?;
        Ok(Self::new(
            &config.display,
            id,
            Kind::Context,
            Some(config.clone()),
            Vec::new(),
        ))
    }
    pub fn parameter(context: &Arc<Self>) -> Result<Arc<Self>> {
        if context.kind != Kind::Context {
            return Err("invalid VPP parameter parent");
        }
        let mut id = va::VA_INVALID_ID;
        // SAFETY: allocation has the exact system pipeline-parameter layout;
        // its content is written before any submission, while exclusively owned.
        checked(unsafe {
            va::vaCreateBuffer(
                context.display.handle,
                context.id,
                va::VABufferType_VAProcPipelineParameterBufferType,
                size_of::<va::VAProcPipelineParameterBuffer>() as u32,
                1,
                ptr::null_mut(),
                &mut id,
            )
        })?;
        Ok(Self::new(
            &context.display,
            id,
            Kind::Buffer,
            Some(context.clone()),
            Vec::new(),
        ))
    }
    pub fn surface(
        display: &Arc<Display>,
        width: u32,
        height: u32,
        rt: u32,
        fourcc: u32,
    ) -> Result<Arc<Self>> {
        if width == 0 || height == 0 || width > 8192 || height > 8192 {
            return Err("invalid surface geometry");
        }
        let mut attr =
            integer_attribute(va::VASurfaceAttribType_VASurfaceAttribPixelFormat, fourcc);
        let mut id = va::VA_INVALID_ID;
        // SAFETY: complete scalar attribute and bounded output allocation.
        checked(unsafe {
            va::vaCreateSurfaces(display.handle, rt, width, height, &mut id, 1, &mut attr, 1)
        })?;
        Ok(Self::new(display, id, Kind::Surface, None, Vec::new()))
    }
    pub fn import(
        display: &Arc<Display>,
        mut descriptor: va::VADRMPRIMESurfaceDescriptor,
        rt: u32,
        fds: Vec<OwnedFd>,
    ) -> Result<Arc<Self>> {
        if fds.is_empty()
            || fds.len() > 4
            || descriptor.num_objects as usize != fds.len()
            || !(1..=4).contains(&descriptor.num_layers)
            || descriptor.width == 0
            || descriptor.height == 0
            || descriptor.width > 8192
            || descriptor.height > 8192
        {
            return Err("invalid DMA-BUF descriptor");
        }
        for layer in &descriptor.layers[..descriptor.num_layers as usize] {
            if !(1..=4).contains(&layer.num_planes)
                || layer.object_index[..layer.num_planes as usize]
                    .iter()
                    .any(|i| *i >= descriptor.num_objects)
            {
                return Err("invalid DMA-BUF layer");
            }
        }
        for (object, fd) in descriptor.objects.iter_mut().zip(&fds) {
            object.fd = fd.as_raw_fd();
        }
        let mut attrs = [
            integer_attribute(
                va::VASurfaceAttribType_VASurfaceAttribMemoryType,
                va::VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2,
            ),
            integer_attribute(
                va::VASurfaceAttribType_VASurfaceAttribPixelFormat,
                descriptor.fourcc,
            ),
            va::VASurfaceAttrib {
                type_: va::VASurfaceAttribType_VASurfaceAttribExternalBufferDescriptor,
                flags: va::VA_SURFACE_ATTRIB_SETTABLE,
                ..Default::default()
            },
        ];
        attrs[2].value.type_ = va::VAGenericValueType_VAGenericValueTypePointer;
        attrs[2].value.value.p = (&raw mut descriptor).cast();
        let mut id = va::VA_INVALID_ID;
        // SAFETY: bounded inline descriptor, valid retained fds and complete
        // attributes remain live throughout the synchronous import call.
        checked(unsafe {
            va::vaCreateSurfaces(
                display.handle,
                rt,
                descriptor.width,
                descriptor.height,
                &mut id,
                1,
                attrs.as_mut_ptr(),
                attrs.len() as u32,
            )
        })?;
        Ok(Self::new(display, id, Kind::Surface, None, fds))
    }
    pub fn wait(&self) -> Result<()> {
        if self.kind != Kind::Surface {
            return Err("not a surface");
        }
        if let Some(sync) = self.display.sync {
            // SAFETY: the owned surface and display remain alive for the bounded wait.
            let result = unsafe { sync(self.display.handle, self.id, 500_000_000) };
            if result != va::VA_STATUS_ERROR_UNIMPLEMENTED as i32 {
                return checked(result);
            }
        }
        let deadline = Instant::now() + Duration::from_millis(500);
        loop {
            let mut status = 0;
            // SAFETY: this owner keeps the queried surface and display alive.
            checked(unsafe {
                va::vaQuerySurfaceStatus(self.display.handle, self.id, &mut status)
            })?;
            if status == va::VASurfaceStatus_VASurfaceReady {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err("GPU completion timeout");
            }
            std::thread::sleep(Duration::from_micros(200));
        }
    }
    // Region/blend pointers must remain live until the caller submits this buffer.
    pub unsafe fn write_parameter(
        &mut self,
        parameters: va::VAProcPipelineParameterBuffer,
    ) -> Result<()> {
        if self.kind != Kind::Buffer {
            return Err("not a pipeline buffer");
        }
        let mapping = Map::new(self, self.id)?;
        // SAFETY: this constructor allocates exactly one parameter record; the
        // caller exclusively owns this context and retains any region pointers
        // until RenderPicture consumes them. Mapping unmaps before submission.
        unsafe {
            mapping
                .pointer
                .as_ptr()
                .cast::<va::VAProcPipelineParameterBuffer>()
                .write(parameters);
        }
        Ok(())
    }
}
impl Drop for Resource {
    fn drop(&mut self) {
        // SAFETY: one owner destroys each successful allocation. The parent,
        // display and imported descriptors remain retained until after this call.
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
pub fn integer_attribute(kind: va::VASurfaceAttribType, value: u32) -> va::VASurfaceAttrib {
    let mut attr = va::VASurfaceAttrib {
        type_: kind,
        flags: va::VA_SURFACE_ATTRIB_SETTABLE,
        ..Default::default()
    };
    attr.value.type_ = va::VAGenericValueType_VAGenericValueTypeInteger;
    attr.value.value.i = value as i32;
    attr
}
pub struct Image {
    info: va::VAImage,
    owner: Arc<Resource>,
}
impl Image {
    pub fn retain(&self) -> Arc<Resource> {
        self.owner.clone()
    }
    pub fn info(&self) -> &va::VAImage {
        &self.info
    }
    pub fn new(display: &Arc<Display>, width: u32, height: u32, fourcc: u32) -> Result<Self> {
        if width == 0 || height == 0 || width > 8192 || height > 8192 {
            return Err("invalid image geometry");
        }
        // SAFETY: display is initialized; query returns an allocation bound only.
        let count = unsafe { va::vaMaxNumImageFormats(display.handle) };
        if !(1..=256).contains(&count) {
            return Err("invalid image format count");
        }
        let mut formats = vec![va::VAImageFormat::default(); count as usize];
        let mut count = count;
        // SAFETY: format output vector has the display's advertised capacity.
        checked(unsafe {
            va::vaQueryImageFormats(display.handle, formats.as_mut_ptr(), &mut count)
        })?;
        if count < 0 || count as usize > formats.len() {
            return Err("invalid image formats");
        }
        let format = formats[..count as usize]
            .iter_mut()
            .find(|f| f.fourcc == fourcc)
            .ok_or("unsupported image format")?;
        let mut info = va::VAImage::default();
        // SAFETY: exact advertised format and bounded dimensions, with live output.
        checked(unsafe {
            va::vaCreateImage(
                display.handle,
                format,
                width as i32,
                height as i32,
                &mut info,
            )
        })?;
        let owner = Resource::new(display, info.image_id, Kind::Image, None, Vec::new());
        if info.data_size == 0
            || info.data_size > 536870912
            || info.num_planes == 0
            || info.num_planes > 3
        {
            return Err("invalid image layout");
        }
        Ok(Self { info, owner })
    }
    pub fn map(&mut self) -> Result<ImageMap<'_>> {
        Ok(ImageMap {
            inner: Map::new(&self.owner, self.info.buf)?,
            length: self.info.data_size as usize,
        })
    }
    pub fn display(&self) -> va::VADisplay {
        self.owner.display.handle
    }
}
struct Map<'a> {
    owner: &'a Resource,
    buffer: u32,
    pointer: NonNull<c_void>,
}
impl<'a> Map<'a> {
    fn new(owner: &'a Resource, buffer: u32) -> Result<Self> {
        let mut pointer = ptr::null_mut();
        // SAFETY: owner retains the mapped buffer or image through this borrow.
        checked(unsafe { va::vaMapBuffer(owner.display.handle, buffer, &mut pointer) })?;
        if let Some(pointer) = NonNull::new(pointer) {
            Ok(Self {
                owner,
                buffer,
                pointer,
            })
        } else {
            // SAFETY: even a successful null map must have a paired unmap.
            unsafe {
                va::vaUnmapBuffer(owner.display.handle, buffer);
            }
            Err("null image mapping")
        }
    }
}
impl Drop for Map<'_> {
    fn drop(&mut self) {
        // SAFETY: this guard owns exactly one live map and retains its resource.
        unsafe {
            va::vaUnmapBuffer(self.owner.display.handle, self.buffer);
        }
    }
}
pub struct ImageMap<'a> {
    inner: Map<'a>,
    length: usize,
}
impl ImageMap<'_> {
    pub fn bytes(&self) -> &[u8] {
        // SAFETY: VA's mapped image allocation is exactly its reported data_size;
        // the borrow retains the image and cannot outlive the matching unmap.
        unsafe { std::slice::from_raw_parts(self.inner.pointer.as_ptr().cast(), self.length) }
    }
    pub fn bytes_mut(&mut self) -> &mut [u8] {
        // SAFETY: mutable map access is exclusive through this guard. Callers
        // own the image and never publish its pointer while it is writable.
        unsafe { std::slice::from_raw_parts_mut(self.inner.pointer.as_ptr().cast(), self.length) }
    }
}
