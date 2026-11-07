// SPDX-License-Identifier: MIT
//! Bounded capture surfaces and checked opaque handles. Frames retain their
//! surface, descriptor and render-node name independently of the capturer.
use crate::{
    ffi,
    va::{self, Display, Image, Resource},
};
use dpe_core::{
    geometry::{self, Rect},
    handles::{Handles, PointerKind},
};
use dpe_os::profiler::Span;
use std::{
    ffi::{CStr, CString, c_char, c_void},
    os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
    ptr,
    sync::{
        Arc, Mutex, MutexGuard, OnceLock,
        atomic::{AtomicU64, Ordering},
    },
};
type Result<T> = va::Result<T>;
const OUTPUTS: usize = 4;
const IMPORTS: usize = 8;
const fn fourcc(bytes: &[u8; 4]) -> u32 {
    u32::from_le_bytes(*bytes)
}
fn lock<T>(value: &Mutex<T>) -> MutexGuard<'_, T> {
    value
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
fn capturers() -> &'static Handles<Arc<Mutex<State>>> {
    static TABLE: OnceLock<Handles<Arc<Mutex<State>>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::new(32))
}
fn frames() -> &'static Handles<Frame> {
    static TABLE: OnceLock<Handles<Frame>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::pointers(256, PointerKind::VideoFrame))
}
fn views() -> &'static Handles<Arc<Output>> {
    static TABLE: OnceLock<Handles<Arc<Output>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::new(256))
}
fn planar_leases() -> &'static Handles<Arc<Output>> {
    static TABLE: OnceLock<Handles<Arc<Output>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::pointers(256, PointerKind::Planar))
}
// A failed completion cannot release GPU objects still being accessed. Retain
// only already active contexts (at most 32), rejecting further creates.
static QUARANTINE: Mutex<Vec<Arc<Mutex<State>>>> = Mutex::new(Vec::new());
static READBACKS: AtomicU64 = AtomicU64::new(0);
#[derive(Clone)]
struct Frame {
    output: Arc<Output>,
    timestamp: u64,
}
#[derive(Default)]
struct Cpu {
    nv12: Vec<u8>,
    i420: Vec<u8>,
    downloaded: bool,
    planar: bool,
    image: Option<Image>,
}
struct Output {
    surface: Arc<Resource>,
    _fd: OwnedFd,
    view: ffi::encoder_nv12,
    node: Arc<CString>,
    cpu: Mutex<Cpu>,
}
#[derive(PartialEq, Eq)]
struct Key {
    cursor: bool,
    width: u32,
    height: u32,
    format: u32,
    planes: u32,
    storage: u32,
    modifier: u64,
    device: [u64; 4],
    inode: [u64; 4],
    offset: [u32; 4],
    stride: [u32; 4],
}
struct Imported {
    key: Key,
    surface: Arc<Resource>,
    serial: u64,
    _cursor_fds: Vec<OwnedFd>,
}
struct Pipeline {
    context: Arc<Resource>,
    parameters: [Arc<Resource>; 2],
    blend: u32,
    size: (u32, u32),
}
struct State {
    display: Arc<Display>,
    config: Arc<Resource>,
    node: Arc<CString>,
    pipeline: Option<Pipeline>,
    imports: Vec<Imported>,
    outputs: Vec<Arc<Output>>,
    serial: u64,
    failed: bool,
    pending_upload: Vec<Arc<Resource>>,
}
fn rect(value: ffi::dpe_direct_rect) -> Rect {
    Rect {
        x: value.x,
        y: value.y,
        width: value.width,
        height: value.height,
    }
}
fn format(drm: u32, cursor: bool) -> Result<(u32, u32)> {
    let color = match drm {
        n if n == fourcc(b"XR24") => ffi::VA_FOURCC_BGRX,
        n if n == fourcc(b"AR24") => {
            if cursor {
                ffi::VA_FOURCC_BGRA
            } else {
                ffi::VA_FOURCC_BGRX
            }
        }
        n if n == fourcc(b"XB24") => ffi::VA_FOURCC_RGBX,
        n if n == fourcc(b"AB24") => {
            if cursor {
                ffi::VA_FOURCC_RGBA
            } else {
                ffi::VA_FOURCC_RGBX
            }
        }
        n if !cursor && (n == fourcc(b"XR30") || n == fourcc(b"AR30")) => {
            return Ok((ffi::VA_FOURCC_X2R10G10B10, ffi::VA_RT_FORMAT_RGB32_10BPP));
        }
        n if !cursor && (n == fourcc(b"XB30") || n == fourcc(b"AB30")) => {
            return Ok((ffi::VA_FOURCC_X2B10G10R10, ffi::VA_RT_FORMAT_RGB32_10BPP));
        }
        _ => return Err("unsupported DRM format"),
    };
    Ok((color, ffi::VA_RT_FORMAT_RGB32))
}
fn metadata(fd: BorrowedFd<'_>) -> Result<libc::stat> {
    // SAFETY: initialized stat output and a live borrowed descriptor.
    let mut stat: libc::stat = unsafe { std::mem::zeroed() };
    // SAFETY: fstat writes only the complete stat output above.
    if unsafe { libc::fstat(fd.as_raw_fd(), &mut stat) } != 0 {
        return Err("descriptor metadata");
    }
    Ok(stat)
}
fn image_layout(image: &Image, width: usize, rows: &[usize]) -> Result<ffi::VAImage> {
    let info = *image.info();
    if info.num_planes as usize != rows.len() {
        return Err("unexpected image planes");
    }
    for (plane, &height) in rows.iter().enumerate() {
        if height == 0
            || (info.pitches[plane] as usize) < width
            || info.offsets[plane] as usize + (height - 1) * info.pitches[plane] as usize + width
                > info.data_size as usize
        {
            return Err("invalid image pitch/extent");
        }
    }
    Ok(info)
}
fn upload_cursor(
    display: &Arc<Display>,
    image: &ffi::dpe_direct_image,
    fd: BorrowedFd<'_>,
) -> Result<(Arc<Resource>, Image)> {
    let stat = metadata(fd)?;
    // SAFETY: read-only fcntl query of the borrowed cursor descriptor.
    let seals = unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_GET_SEALS) };
    let required = libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK;
    if image.planes != 1
        || image.width > 256
        || image.height > 256
        || image.format != fourcc(b"AR24")
        || image.offset[0] != 0
        || image.stride[0] != image.width * 4
        || seals < 0
        || seals & required != required
        || stat.st_size != i64::from(image.width) * i64::from(image.height) * 4
    {
        return Err("invalid sealed cursor bitmap");
    }
    // Seals prevent shrinking or changing this small bitmap while it is read.
    let length = stat.st_size as usize;
    // SAFETY: valid sealed fd, positive checked extent and a private read-only mapping.
    let pointer = unsafe {
        libc::mmap(
            ptr::null_mut(),
            length,
            libc::PROT_READ,
            libc::MAP_PRIVATE,
            fd.as_raw_fd(),
            0,
        )
    };
    if pointer == libc::MAP_FAILED {
        return Err("map cursor bitmap");
    }
    struct Mapping(*mut c_void, usize);
    impl Drop for Mapping {
        fn drop(&mut self) {
            // SAFETY: guard owns the one successful mmap of this exact length.
            unsafe {
                libc::munmap(self.0, self.1);
            }
        }
    }
    let source = Mapping(pointer, length);
    // SAFETY: mapping is sealed, read-only and retained through this borrow.
    let bytes = unsafe { std::slice::from_raw_parts(source.0.cast::<u8>(), length) };
    let mut upload = Image::new(display, image.width, image.height, ffi::VA_FOURCC_BGRA)?;
    let info = image_layout(&upload, image.width as usize * 4, &[image.height as usize])?;
    {
        let mut mapped = upload.map()?;
        for row in 0..image.height as usize {
            let begin = info.offsets[0] as usize + row * info.pitches[0] as usize;
            let source = row * image.stride[0] as usize;
            mapped.bytes_mut()[begin..begin + image.stride[0] as usize]
                .copy_from_slice(&bytes[source..source + image.stride[0] as usize]);
        }
    }
    let surface = Resource::surface(
        display,
        image.width,
        image.height,
        ffi::VA_RT_FORMAT_RGB32,
        ffi::VA_FOURCC_BGRA,
    )?;
    Ok((surface, upload))
}
impl State {
    fn new(node: CString) -> Result<Self> {
        let display = Display::open(&node)?;
        let config = Resource::config(&display)?;
        Ok(Self {
            display,
            config,
            node: Arc::new(node),
            pipeline: None,
            imports: Vec::with_capacity(IMPORTS),
            outputs: Vec::with_capacity(OUTPUTS),
            serial: 0,
            failed: false,
            pending_upload: Vec::with_capacity(2),
        })
    }
    fn upload(
        &mut self,
        surface: &Arc<Resource>,
        image: &Image,
        width: u32,
        height: u32,
        top: u32,
    ) -> Result<()> {
        // Retain both sides before submitting anything. On a failed/unknown
        // completion the containing State is quarantined with these resources.
        self.pending_upload.push(surface.clone());
        self.pending_upload.push(image.retain());
        // SAFETY: image/target owners and checked geometry remain retained through completion.
        let submitted = va::checked(unsafe {
            ffi::vaPutImage(
                self.display.handle(),
                surface.id(),
                image.info().image_id,
                0,
                0,
                width,
                height,
                0,
                top as i32,
                width,
                height,
            )
        });
        if submitted.is_err() || surface.wait().is_err() {
            self.failed = true;
            return Err("image upload completion failed");
        }
        self.pending_upload.clear();
        Ok(())
    }
    fn import(
        &mut self,
        image: &ffi::dpe_direct_image,
        fds: &[BorrowedFd<'_>],
        cursor: bool,
    ) -> Result<Arc<Resource>> {
        if !(1..=4).contains(&image.planes)
            || !rect(image.source).contained((image.width, image.height))
            || image.padding != 0
            || image.storage > 1
            || (!cursor && image.storage != 0)
        {
            return Err("invalid source image");
        }
        let (fourcc, rt) = format(image.format, cursor)?;
        let mut key = Key {
            cursor,
            width: image.width,
            height: image.height,
            format: image.format,
            planes: image.planes,
            storage: image.storage,
            modifier: image.modifier,
            device: [0; 4],
            inode: [0; 4],
            offset: image.offset,
            stride: image.stride,
        };
        let mut descriptor = ffi::VADRMPRIMESurfaceDescriptor {
            fourcc,
            width: image.width,
            height: image.height,
            num_objects: image.planes,
            num_layers: 1,
            ..Default::default()
        };
        descriptor.layers[0].drm_format = image.format;
        descriptor.layers[0].num_planes = image.planes;
        for index in 0..image.planes as usize {
            let fd = fds
                .get(image.fd[index] as usize)
                .ok_or("descriptor index")?;
            let stat = metadata(*fd)?;
            if stat.st_size <= 0
                || stat.st_size > i64::from(u32::MAX)
                || image.stride[index] == 0
                || image.stride[index] > 262144
                || u64::from(image.offset[index]) >= stat.st_size as u64
            {
                return Err("invalid source plane");
            }
            key.device[index] = stat.st_dev;
            key.inode[index] = stat.st_ino;
            descriptor.objects[index].size = stat.st_size as u32;
            descriptor.objects[index].drm_format_modifier = image.modifier;
            descriptor.layers[0].object_index[index] = index as u32;
            descriptor.layers[0].offset[index] = image.offset[index];
            descriptor.layers[0].pitch[index] = image.stride[index];
        }
        self.serial = self.serial.wrapping_add(1);
        if let Some(entry) = self.imports.iter_mut().find(|entry| entry.key == key) {
            entry.serial = self.serial;
            return Ok(entry.surface.clone());
        }
        // Duplicate only cache misses. Keeping those descriptors alive prevents
        // inode reuse from turning a stale cache entry into another allocation.
        let owners = (0..image.planes as usize)
            .map(|index| {
                fds[image.fd[index] as usize]
                    .try_clone_to_owned()
                    .map_err(|_| "retain source descriptor")
            })
            .collect::<Result<Vec<_>>>()?;
        let (surface, cursor_fds) = if image.storage == 1 {
            let (surface, upload) = upload_cursor(&self.display, image, owners[0].as_fd())?;
            self.upload(&surface, &upload, image.width, image.height, 0)?;
            (surface, owners)
        } else {
            (
                Resource::import(&self.display, descriptor, rt, owners)?,
                Vec::new(),
            )
        };
        if self.imports.len() == IMPORTS {
            let oldest = self
                .imports
                .iter()
                .enumerate()
                .min_by_key(|(_, entry)| entry.serial)
                .map(|(i, _)| i)
                .ok_or("import cache")?;
            self.imports.swap_remove(oldest);
        }
        self.imports.push(Imported {
            key,
            surface: surface.clone(),
            serial: self.serial,
            _cursor_fds: cursor_fds,
        });
        Ok(surface)
    }
    fn output(&mut self, width: u32, height: u32) -> Result<Arc<Output>> {
        let _profile = Span::new(7);
        let available = self
            .outputs
            .iter()
            .position(|output| Arc::strong_count(output) == 1);
        if available.is_none() && self.outputs.len() == OUTPUTS {
            return Err("pool full");
        }
        let index = available.unwrap_or(self.outputs.len());
        if let Some(output) = self.outputs.get_mut(index)
            && output.view.width == width
            && output.view.height == height
        {
            let output = Arc::get_mut(output).ok_or("output is leased")?;
            let cpu = output
                .cpu
                .get_mut()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            cpu.downloaded = false;
            cpu.planar = false;
            return Ok(self.outputs[index].clone());
        }
        let allocated_height = height.next_multiple_of(16);
        let surface = Resource::surface(
            &self.display,
            width,
            allocated_height,
            ffi::VA_RT_FORMAT_YUV420,
            ffi::VA_FOURCC_NV12,
        )?;
        if allocated_height != height {
            let rows = allocated_height - height;
            let mut image = Image::new(&self.display, width, rows, ffi::VA_FOURCC_NV12)?;
            let info = image_layout(&image, width as usize, &[rows as usize, rows as usize / 2])?;
            {
                let mut map = image.map()?;
                for (plane, rows) in [rows, rows / 2].into_iter().enumerate() {
                    for row in 0..rows as usize {
                        let start =
                            info.offsets[plane] as usize + row * info.pitches[plane] as usize;
                        map.bytes_mut()[start..start + width as usize].fill(if plane == 0 {
                            16
                        } else {
                            128
                        });
                    }
                }
            }
            self.upload(&surface, &image, width, rows, height)?;
        }
        let mut descriptor = ffi::VADRMPRIMESurfaceDescriptor::default();
        // SAFETY: live owned surface and complete stack descriptor output.
        va::checked(unsafe {
            ffi::vaExportSurfaceHandle(
                self.display.handle(),
                surface.id(),
                ffi::VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2,
                ffi::VA_EXPORT_SURFACE_READ_ONLY | ffi::VA_EXPORT_SURFACE_COMPOSED_LAYERS,
                (&raw mut descriptor).cast(),
            )
        })?;
        // Adopt every exported descriptor before validating any driver metadata.
        let mut owners = Vec::with_capacity(4);
        for object in descriptor
            .objects
            .iter()
            .take((descriptor.num_objects as usize).min(4))
        {
            if object.fd < 0 {
                continue;
            }
            // SAFETY: vaExportSurfaceHandle transfers ownership of each object fd.
            let fd = unsafe { OwnedFd::from_raw_fd(object.fd) };
            owners.push(fd);
        }
        if owners.len() != descriptor.num_objects as usize {
            return Err("invalid exported descriptor");
        }
        for fd in &owners {
            // SAFETY: set close-on-exec on our newly owned descriptor.
            if unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC) } != 0 {
                return Err("export descriptor flags");
            }
        }
        let layer = &descriptor.layers[0];
        if descriptor.num_objects != 1
            || descriptor.num_layers != 1
            || layer.num_planes != 2
            || layer.drm_format != fourcc(b"NV12")
            || layer.object_index[0] != 0
            || layer.object_index[1] != 0
        {
            return Err("unsupported exported NV12 layout");
        }
        let fd = owners.pop().ok_or("missing exported descriptor")?;
        let view = ffi::encoder_nv12 {
            width,
            height,
            fd: fd.as_raw_fd(),
            size: descriptor.objects[0].size,
            offset: [layer.offset[0], layer.offset[1]],
            stride: [layer.pitch[0], layer.pitch[1]],
            modifier: descriptor.objects[0].drm_format_modifier,
        };
        let output = Arc::new(Output {
            surface,
            _fd: fd,
            view,
            node: self.node.clone(),
            cpu: Mutex::new(Cpu::default()),
        });
        if index == self.outputs.len() {
            self.outputs.push(output.clone());
        } else {
            self.outputs[index] = output.clone();
        }
        Ok(output)
    }
    fn pipeline(&mut self, width: u32, height: u32) -> Result<()> {
        if self
            .pipeline
            .as_ref()
            .is_some_and(|pipeline| pipeline.size == (width, height))
        {
            return Ok(());
        }
        let context = Resource::context(&self.config, width, height.next_multiple_of(16))?;
        let parameters = [
            Resource::parameter(&context)?,
            Resource::parameter(&context)?,
        ];
        let mut caps = ffi::VAProcPipelineCaps::default();
        // SAFETY: initialized context, zero filters and complete caps output.
        va::checked(unsafe {
            ffi::vaQueryVideoProcPipelineCaps(
                self.display.handle(),
                context.id(),
                ptr::null_mut(),
                0,
                &mut caps,
            )
        })?;
        self.pipeline = Some(Pipeline {
            context,
            parameters,
            blend: caps.blend_flags,
            size: (width, height),
        });
        Ok(())
    }
    fn convert(
        &mut self,
        packet: &ffi::dpe_direct_packet,
        fds: &[BorrowedFd<'_>],
        width: u32,
        height: u32,
    ) -> Result<Frame> {
        if self.failed {
            return Err("capture context failed");
        }
        let output = self.output(width, height)?;
        self.pipeline(width, height)?;
        let primary;
        let cursor;
        {
            let _profile = Span::new(6);
            primary = self.import(&packet.image, fds, false)?;
            cursor = if packet.cursor.planes > 0 {
                Some(self.import(&packet.cursor, fds, true)?)
            } else {
                None
            };
        }
        let pipeline = self.pipeline.as_mut().ok_or("missing VPP context")?;
        // SAFETY: caller serializes this context and holds all source/target resources.
        va::checked(unsafe {
            ffi::vaBeginPicture(
                self.display.handle(),
                pipeline.context.id(),
                output.surface.id(),
            )
        })?;
        let mut valid = draw(
            pipeline,
            0,
            &primary,
            &packet.image,
            (packet.width, packet.height),
            (width, height),
            false,
        );
        if valid.is_ok()
            && let Some(cursor) = &cursor
        {
            valid = draw(
                pipeline,
                1,
                cursor,
                &packet.cursor,
                (packet.width, packet.height),
                (width, height),
                true,
            );
        }
        // Always close a successfully begun picture, including failed draw.
        // SAFETY: context has one successfully begun picture and no concurrent submitter.
        let ended =
            va::checked(unsafe { ffi::vaEndPicture(self.display.handle(), pipeline.context.id()) });
        let completed = {
            let _profile = Span::new(9);
            output.surface.wait()
        };
        if ended.is_err() || completed.is_err() {
            self.failed = true;
            return Err("VPP completion failed");
        }
        valid?;
        Ok(Frame {
            output,
            timestamp: packet.timestamp_ns,
        })
    }
}
fn draw(
    pipeline: &mut Pipeline,
    index: usize,
    input: &Arc<Resource>,
    image: &ffi::dpe_direct_image,
    canvas: (u32, u32),
    target: (u32, u32),
    cursor: bool,
) -> Result<()> {
    let blit = geometry::scaled_blit(
        (image.width, image.height),
        rect(image.source),
        rect(image.destination),
        canvas,
        target,
    )
    .map_err(|_| "invalid VPP rectangle")?;
    let Some(blit) = blit else {
        return if cursor {
            Ok(())
        } else {
            Err("invisible primary image")
        };
    };
    if cursor && pipeline.blend & ffi::VA_BLEND_PREMULTIPLIED_ALPHA == 0 {
        return Err("cursor blending unsupported");
    }
    let mut source = ffi::VARectangle {
        x: blit.source.x as i16,
        y: blit.source.y as i16,
        width: blit.source.width as u16,
        height: blit.source.height as u16,
    };
    let mut destination = ffi::VARectangle {
        x: blit.destination.x as i16,
        y: blit.destination.y as i16,
        width: blit.destination.width as u16,
        height: blit.destination.height as u16,
    };
    let mut blend = ffi::VABlendState {
        flags: ffi::VA_BLEND_PREMULTIPLIED_ALPHA,
        global_alpha: 1.0,
        ..Default::default()
    };
    let parameters = ffi::VAProcPipelineParameterBuffer {
        surface: input.id(),
        surface_region: &mut source,
        output_region: &mut destination,
        surface_color_standard: ffi::_VAProcColorStandardType_VAProcColorStandardBT709,
        output_color_standard: ffi::_VAProcColorStandardType_VAProcColorStandardBT709,
        output_background_color: if cursor { 0 } else { 0xff000000 },
        blend_state: if cursor { &mut blend } else { ptr::null_mut() },
        ..Default::default()
    };
    let _profile = Span::new(8);
    let parameter =
        Arc::get_mut(&mut pipeline.parameters[index]).ok_or("pipeline buffer is shared")?;
    // SAFETY: all parameter pointers refer to this stack, live through RenderPicture;
    // the buffer has unique ownership and this context has one serialized caller.
    unsafe {
        parameter.write_parameter(parameters)?;
    }
    let mut buffer = parameter.id();
    // SAFETY: initialized parameter buffer and regions, consumed synchronously by this call.
    va::checked(unsafe {
        ffi::vaRenderPicture(
            input.display().handle(),
            pipeline.context.id(),
            &mut buffer,
            1,
        )
    })
}
impl Output {
    fn download(&self, cpu: &mut Cpu) -> Result<()> {
        if cpu.downloaded {
            return Ok(());
        }
        let _profile = Span::new(26);
        let (w, h) = (self.view.width as usize, self.view.height as usize);
        if cpu.image.is_none() {
            cpu.image = Some(Image::new(
                self.surface.display(),
                w as u32,
                h as u32,
                ffi::VA_FOURCC_NV12,
            )?);
        }
        let image = cpu.image.as_mut().ok_or("missing readback image")?;
        let info = image_layout(image, w, &[h, h / 2])?;
        // SAFETY: completed owned surface and matching image, all geometry/layout checked.
        va::checked(unsafe {
            ffi::vaGetImage(
                image.display(),
                self.surface.id(),
                0,
                0,
                w as u32,
                h as u32,
                info.image_id,
            )
        })?;
        let mapped = image.map()?;
        cpu.nv12.resize(w * h * 3 / 2, 0);
        for (plane, rows) in [h, h / 2].into_iter().enumerate() {
            for row in 0..rows {
                let source = info.offsets[plane] as usize + row * info.pitches[plane] as usize;
                let target = if plane == 0 { row * w } else { w * h + row * w };
                cpu.nv12[target..target + w].copy_from_slice(&mapped.bytes()[source..source + w]);
            }
        }
        cpu.downloaded = true;
        READBACKS.fetch_add(1, Ordering::Relaxed);
        Ok(())
    }
}
fn errno(value: i32) {
    // SAFETY: errno is the calling thread's own writable C error slot.
    unsafe {
        *libc::__errno_location() = value;
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_gpu_create(node: *const c_char) -> *mut c_void {
    let quarantine = lock(&QUARANTINE);
    if node.is_null() || !quarantine.is_empty() {
        return ptr::null_mut();
    }
    // SAFETY: C caller supplies a live NUL-terminated node name.
    let node = unsafe { CStr::from_ptr(node) };
    if node.to_bytes().len() >= 4096 {
        return ptr::null_mut();
    }
    let Ok(state) = State::new(node.to_owned()) else {
        return ptr::null_mut();
    };
    capturers()
        .insert(Arc::new(Mutex::new(state)))
        .map_or(ptr::null_mut(), |token| token.get() as *mut c_void)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_gpu_destroy(handle: *mut c_void) {
    drop(capturers().remove(handle as usize));
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_gpu_convert(
    handle: *mut c_void,
    packet: *const ffi::dpe_direct_packet,
    fds: *const i32,
    width: u32,
    height: u32,
) -> *mut c_void {
    errno(libc::EINVAL);
    let Some(state) = capturers().get(handle as usize) else {
        return ptr::null_mut();
    };
    if packet.is_null() || fds.is_null() {
        return ptr::null_mut();
    }
    // SAFETY: C ABI supplies one initialized packet and nine readable descriptor slots.
    let (packet, raw_fds) = unsafe { (&*packet, std::slice::from_raw_parts(fds, 9)) };
    // SAFETY: packet's complete initialized C representation has the reviewed 296-byte layout.
    let bytes = unsafe {
        std::slice::from_raw_parts(
            packet as *const _ as *const u8,
            size_of::<ffi::dpe_direct_packet>(),
        )
    };
    if !dpe_os::wire::header(bytes)
        || packet.type_ != 2
        || width == 0
        || height == 0
        || width > 8192
        || height > 4320
        || (width | height) & 1 != 0
        || packet.width == 0
        || packet.height == 0
        || packet.width > 8192
        || packet.height > 4320
        || packet.image.destination.x != 0
        || packet.image.destination.y != 0
        || packet.image.destination.width != packet.width
        || packet.image.destination.height != packet.height
    {
        return ptr::null_mut();
    }
    if packet.descriptors == 0 || raw_fds[0] < 0 {
        return ptr::null_mut();
    }
    // SAFETY: the first source fd is live for this call; unused array entries
    // borrow it too, and only the declared prefix reaches the importer.
    let mut borrowed = [unsafe { BorrowedFd::borrow_raw(raw_fds[0]) }; 9];
    for (target, &raw) in borrowed
        .iter_mut()
        .zip(&raw_fds[..packet.descriptors as usize])
    {
        if raw < 0 {
            return ptr::null_mut();
        }
        // SAFETY: descriptors remain borrowed from the caller until conversion and completion return.
        *target = unsafe { BorrowedFd::borrow_raw(raw) };
    }
    let result = {
        lock(&state).convert(
            packet,
            &borrowed[..packet.descriptors as usize],
            width,
            height,
        )
    };
    match result {
        Ok(frame) => frames().insert(frame).map_or_else(
            |_| {
                errno(libc::EAGAIN);
                ptr::null_mut()
            },
            |token| token.get() as *mut c_void,
        ),
        Err(reason) => {
            if lock(&state).failed {
                let mut quarantine = lock(&QUARANTINE);
                if !quarantine.iter().any(|entry| Arc::ptr_eq(entry, &state)) {
                    quarantine.push(state);
                }
            }
            errno(if reason == "pool full" {
                libc::EAGAIN
            } else {
                libc::EIO
            });
            ptr::null_mut()
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_gpu_readbacks() -> u64 {
    READBACKS.load(Ordering::Relaxed)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_frame_owned(handle: *const c_void) -> bool {
    frames().contains(handle as usize)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_frame_destroy(handle: *mut c_void) -> bool {
    frames().remove(handle as usize).is_some()
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_frame_data(
    handle: *const c_void,
    width: *mut usize,
    height: *mut usize,
    timestamp: *mut u64,
) -> bool {
    let Some(frame) = frames().get(handle as usize) else {
        return false;
    };
    if width.is_null() || height.is_null() || timestamp.is_null() {
        return false;
    }
    // SAFETY: caller owns three writable scalar outputs; frame snapshot retains metadata.
    unsafe {
        *width = frame.output.view.width as usize;
        *height = frame.output.view.height as usize;
        *timestamp = frame.timestamp;
    }
    true
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_frame_view(
    handle: *mut c_void,
    view: *mut ffi::encoder_nv12,
    lease: *mut *mut c_void,
    node: *mut *const c_char,
) -> bool {
    let Some(frame) = frames().get(handle as usize) else {
        return false;
    };
    if view.is_null() || lease.is_null() {
        return false;
    }
    let output = frame.output;
    let Ok(token) = views().insert(output.clone()) else {
        return false;
    };
    // SAFETY: owned writable C outputs; lease retains the fd and stable per-capturer CString.
    unsafe {
        *view = output.view;
        *lease = token.get() as *mut c_void;
        if !node.is_null() {
            *node = output.node.as_ptr();
        }
    }
    true
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_frame_release(lease: *mut c_void) {
    drop(views().remove(lease as usize));
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_i420_destroy(lease: *mut c_void) -> bool {
    planar_leases().remove(lease as usize).is_some()
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_frame_cpu_nv12(
    handle: *mut c_void,
    pixels: *mut *const u8,
    size: *mut usize,
    sy: *mut usize,
    suv: *mut usize,
) -> bool {
    let Some(frame) = frames().get(handle as usize) else {
        return false;
    };
    if pixels.is_null() || size.is_null() || sy.is_null() || suv.is_null() {
        return false;
    }
    let mut cpu = lock(&frame.output.cpu);
    if frame.output.download(&mut cpu).is_err() {
        return false;
    }
    // SAFETY: caller's frame ownership retains immutable CPU storage until frame destruction.
    unsafe {
        *pixels = cpu.nv12.as_ptr();
        *size = cpu.nv12.len();
        *sy = frame.output.view.width as usize;
        *suv = *sy;
    }
    true
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_frame_i420(
    handle: *mut c_void,
    pixels: *mut *const u8,
    size: *mut usize,
    sy: *mut usize,
    suv: *mut usize,
    u: *mut usize,
    v: *mut usize,
) -> *mut c_void {
    let Some(frame) = frames().get(handle as usize) else {
        return ptr::null_mut();
    };
    if pixels.is_null()
        || size.is_null()
        || sy.is_null()
        || suv.is_null()
        || u.is_null()
        || v.is_null()
    {
        return ptr::null_mut();
    }
    let output = frame.output;
    let mut cpu = lock(&output.cpu);
    if output.download(&mut cpu).is_err() {
        return ptr::null_mut();
    }
    let (w, h) = (output.view.width as usize, output.view.height as usize);
    if !cpu.planar {
        cpu.i420.resize(w * h * 3 / 2, 0);
        let Cpu { nv12, i420, .. } = &mut *cpu;
        i420[..w * h].copy_from_slice(&nv12[..w * h]);
        for index in 0..w * h / 4 {
            i420[w * h + index] = nv12[w * h + 2 * index];
            i420[w * h * 5 / 4 + index] = nv12[w * h + 2 * index + 1];
        }
        cpu.planar = true;
    }
    let Ok(token) = planar_leases().insert(output.clone()) else {
        return ptr::null_mut();
    };
    // SAFETY: writable C outputs and a newly published lease retaining immutable pixels.
    unsafe {
        *pixels = cpu.i420.as_ptr();
        *size = cpu.i420.len();
        *sy = w;
        *suv = w / 2;
        *u = w * h;
        *v = w * h * 5 / 4;
    }
    token.get() as *mut c_void
}
