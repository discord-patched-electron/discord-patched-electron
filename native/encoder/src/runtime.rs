// SPDX-License-Identifier: MIT
//! Every foreign allocation has one owner. Session handles never expose Rust
//! addresses, packets leave the codec lock before callbacks, and imported input
//! leases follow the last FFmpeg reference rather than submission completion.
use crate::{ffi as f, policy};
use dpe_core::{
    admission::{Admission, lock},
    av1_crop::{Crop, insertion_position},
    handles::{Handles, PointerKind},
};
use dpe_os::profiler::Span;
use std::{
    ffi::{CStr, CString, c_void},
    os::unix::fs::{FileTypeExt, MetadataExt},
    ptr::{self, NonNull},
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicU64, Ordering},
    },
};
const PENDING: usize = 4;
type Result<T> = std::result::Result<T, ()>;

struct Buffer(NonNull<f::AVBufferRef>);
impl Buffer {
    fn adopt(pointer: *mut f::AVBufferRef) -> Result<Self> {
        NonNull::new(pointer).map(Self).ok_or(())
    }
    fn reference(&self) -> Result<Self> {
        // SAFETY: this owner holds a live reference until av_buffer_ref returns.
        Self::adopt(unsafe { f::av_buffer_ref(self.0.as_ptr()) })
    }
    fn into_raw(self) -> *mut f::AVBufferRef {
        let pointer = self.0.as_ptr();
        std::mem::forget(self);
        pointer
    }
    fn display(&self) -> f::VADisplay {
        // SAFETY: only successfully created VAAPI devices use this method.
        unsafe {
            (*((*self.0.as_ptr()).data.cast::<f::AVHWDeviceContext>()))
                .hwctx
                .cast::<f::AVVAAPIDeviceContext>()
                .as_ref()
                .expect("VAAPI hwctx")
                .display
        }
    }
}
impl Drop for Buffer {
    fn drop(&mut self) {
        let mut pointer = self.0.as_ptr();
        // SAFETY: exactly one owned AVBufferRef is released; shared data stays referenced.
        unsafe {
            f::av_buffer_unref(&mut pointer);
        }
    }
}
struct Frame(NonNull<f::AVFrame>);
impl Frame {
    fn new() -> Result<Self> {
        // SAFETY: allocation returns a unique initialized frame or null.
        NonNull::new(unsafe { f::av_frame_alloc() })
            .map(Self)
            .ok_or(())
    }
    fn clear(&mut self) {
        // SAFETY: this exclusive owner releases the current frame's references.
        unsafe {
            f::av_frame_unref(self.0.as_ptr());
        }
    }
}
impl Drop for Frame {
    fn drop(&mut self) {
        let mut pointer = self.0.as_ptr();
        // SAFETY: exactly one allocated frame is owned here.
        unsafe {
            f::av_frame_free(&mut pointer);
        }
    }
}
struct Packet(NonNull<f::AVPacket>);
impl Packet {
    fn new() -> Result<Self> {
        // SAFETY: allocation returns a unique initialized packet or null.
        NonNull::new(unsafe { f::av_packet_alloc() })
            .map(Self)
            .ok_or(())
    }
    fn clear(&mut self) {
        // SAFETY: exclusively held packet, with no outstanding borrowed byte slices.
        unsafe {
            f::av_packet_unref(self.0.as_ptr());
        }
    }
}
impl Drop for Packet {
    fn drop(&mut self) {
        let mut pointer = self.0.as_ptr();
        // SAFETY: uniquely owned allocation; FFmpeg releases its buffer references.
        unsafe {
            f::av_packet_free(&mut pointer);
        }
    }
}
struct Context(NonNull<f::AVCodecContext>);
impl Drop for Context {
    fn drop(&mut self) {
        let mut pointer = self.0.as_ptr();
        // SAFETY: this owner is the sole codec context owner, serialized by its session.
        unsafe {
            f::avcodec_free_context(&mut pointer);
        }
    }
}
struct Filter {
    display: f::VADisplay,
    config: f::VAConfigID,
    context: f::VAContextID,
}
impl Drop for Filter {
    fn drop(&mut self) {
        // SAFETY: successful config/context creation transferred both resources;
        // the containing codec keeps its device alive after this field drops.
        unsafe {
            f::vaDestroyContext(self.display, self.context);
            f::vaDestroyConfig(self.display, self.config);
        }
    }
}
struct Imports(*mut f::dpe_import_cache);
impl Drop for Imports {
    fn drop(&mut self) {
        // SAFETY: this unique cache owner retires lookup ownership, not live leases.
        unsafe {
            f::dpe_import_cache_close(self.0);
        }
    }
}
struct ProducerLease {
    value: *mut c_void,
    release: unsafe extern "C" fn(*mut c_void),
}
impl Drop for ProducerLease {
    fn drop(&mut self) {
        // SAFETY: the API transfers exactly one lease even on submission failure.
        unsafe {
            (self.release)(self.value);
        }
    }
}
enum Imported {
    Cached(*mut f::dpe_import_entry),
    Transient { device: Buffer, surface: u32 },
}
impl Drop for Imported {
    fn drop(&mut self) {
        // SAFETY: variants own either one distinct cache lease or one VA surface.
        unsafe {
            match self {
                Self::Cached(entry) => f::dpe_import_put(*entry),
                Self::Transient { device, surface } => {
                    f::vaDestroySurfaces(device.display(), surface, 1);
                }
            }
        }
    }
}
struct SurfaceLease {
    _surface: Imported,
    _producer: ProducerLease,
}
unsafe extern "C" fn release_surface(value: *mut c_void, _: *mut u8) {
    // SAFETY: av_buffer_create owns this unique Box and invokes its free function once.
    drop(unsafe { Box::from_raw(value.cast::<SurfaceLease>()) });
}
unsafe extern "C" fn release_source(value: *mut c_void, _: *mut u8) {
    let mut source = value.cast::<f::AVFrame>();
    // SAFETY: this opaque reference owns precisely one av_frame_clone allocation.
    unsafe {
        f::av_frame_free(&mut source);
    }
}
unsafe extern "C" fn import_create(
    device: *mut c_void,
    input: *const f::encoder_nv12,
    output: *mut usize,
) -> bool {
    // SAFETY: cache callbacks borrow the retained device and checked descriptor;
    // all fields are initialized, including each union discriminator.
    unsafe {
        let device = &*device.cast::<f::AVBufferRef>();
        let display = (*(*device.data.cast::<f::AVHWDeviceContext>())
            .hwctx
            .cast::<f::AVVAAPIDeviceContext>())
        .display;
        let input = &*input;
        let mut descriptor = f::VADRMPRIMESurfaceDescriptor {
            fourcc: f::VA_FOURCC_NV12,
            width: input.width,
            height: input.height,
            num_objects: 1,
            num_layers: 1,
            ..Default::default()
        };
        descriptor.objects[0].fd = input.fd;
        descriptor.objects[0].size = input.size;
        descriptor.objects[0].drm_format_modifier = input.modifier;
        descriptor.layers[0].drm_format = f::VA_FOURCC_NV12;
        descriptor.layers[0].num_planes = 2;
        for plane in 0..2 {
            descriptor.layers[0].offset[plane] = input.offset[plane];
            descriptor.layers[0].pitch[plane] = input.stride[plane];
        }
        let mut attributes: [f::VASurfaceAttrib; 2] = Default::default();
        attributes[0].type_ = f::VASurfaceAttribMemoryType;
        attributes[0].flags = f::VA_SURFACE_ATTRIB_SETTABLE;
        attributes[0].value.type_ = f::VAGenericValueTypeInteger;
        attributes[0].value.value.i = f::VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2 as i32;
        attributes[1].type_ = f::VASurfaceAttribExternalBufferDescriptor;
        attributes[1].flags = f::VA_SURFACE_ATTRIB_SETTABLE;
        attributes[1].value.type_ = f::VAGenericValueTypePointer;
        attributes[1].value.value.p = (&raw mut descriptor).cast();
        let mut surface = 0;
        if f::vaCreateSurfaces(
            display,
            f::VA_RT_FORMAT_YUV420,
            input.width,
            input.height,
            &mut surface,
            1,
            attributes.as_mut_ptr(),
            2,
        ) != 0
        {
            return false;
        }
        *output = surface as usize;
        true
    }
}
unsafe extern "C" fn import_destroy(device: *mut c_void, surface: usize) {
    // SAFETY: a successful import owns one surface; the cache retains this device.
    unsafe {
        let device = &*device.cast::<f::AVBufferRef>();
        let display = (*(*device.data.cast::<f::AVHWDeviceContext>())
            .hwctx
            .cast::<f::AVVAAPIDeviceContext>())
        .display;
        let mut surface = surface as u32;
        f::vaDestroySurfaces(display, &mut surface, 1);
    }
}
unsafe extern "C" fn import_finish(device: *mut c_void) {
    let mut device = device.cast::<f::AVBufferRef>();
    // SAFETY: successful cache creation transferred this one owned device reference.
    unsafe {
        f::av_buffer_unref(&mut device);
    }
}

#[derive(Clone, Copy)]
struct Pending {
    pts: i64,
    meta: f::encoder_frame_meta,
    crop: Crop,
}
enum Event {
    Encoded {
        meta: f::encoder_frame_meta,
        packet: Packet,
        info: u64,
    },
    Dropped(f::encoder_frame_meta),
}
#[derive(Default)]
struct Events {
    items: [Option<Event>; PENDING * 2],
    count: usize,
}
impl Events {
    fn push(&mut self, event: Event) -> Result<()> {
        let slot = self.items.get_mut(self.count).ok_or(())?;
        *slot = Some(event);
        self.count += 1;
        Ok(())
    }
}
// Declaration order is destruction order: codec, frame leases, pools/imports,
// filter resources and finally the device. Partial construction follows it too.
struct Codec {
    context: Context,
    input: Frame,
    cpu: Option<Frame>,
    black: Option<Frame>,
    frames: Buffer,
    upload: Option<Buffer>,
    imports: Option<Imports>,
    filter: Option<Filter>,
    device: Buffer,
    packets: Vec<Packet>,
    initialized: Vec<u32>,
    pending: [Option<Pending>; PENDING],
    opened: f::encoder_dimensions,
    encoded: f::encoder_dimensions,
    generation: u64,
}
impl Codec {
    fn initialize_padding(&mut self, output: &mut Frame) -> Result<()> {
        // SAFETY: both frames own FFmpeg-allocated, validated NV12 buffers. Row
        // writes stay within visible width and the allocator's positive stride.
        unsafe {
            let output = &mut *output.0.as_ptr();
            let surface = output.data[3] as usize as u32;
            if self.initialized.contains(&surface) {
                return Ok(());
            }
            // The codec/picture pool is bounded. Unexpected surface churn must
            // not turn a long-running stream into unbounded bookkeeping.
            if self.initialized.len() >= 128 {
                return Err(());
            }
            if self.black.is_none() {
                let black = Frame::new()?;
                let frame = &mut *black.0.as_ptr();
                frame.format = f::AV_PIX_FMT_NV12;
                frame.width = output.width;
                frame.height = output.height;
                if f::av_frame_get_buffer(black.0.as_ptr(), 32) < 0 {
                    return Err(());
                }
                for plane in 0..2 {
                    let rows = frame.height as usize / if plane == 0 { 1 } else { 2 };
                    if frame.linesize[plane] < frame.width || frame.data[plane].is_null() {
                        return Err(());
                    }
                    for row in 0..rows {
                        ptr::write_bytes(
                            frame.data[plane].add(row * frame.linesize[plane] as usize),
                            if plane == 0 { 16 } else { 128 },
                            frame.width as usize,
                        );
                    }
                }
                self.black = Some(black);
            }
            if f::av_hwframe_transfer_data(output, self.black.as_ref().ok_or(())?.0.as_ptr(), 0) < 0
            {
                return Err(());
            }
            self.initialized.push(surface);
            Ok(())
        }
    }
    fn filter(&mut self) -> Result<()> {
        let _profile = Span::new(11);
        if self.filter.is_none() {
            return Ok(());
        }
        let mut output = Frame::new()?;
        // SAFETY: frame/pool references are held throughout submission. The
        // opaque_ref clone pins producer input until all output references end.
        unsafe {
            if f::av_hwframe_get_buffer(self.frames.0.as_ptr(), output.0.as_ptr(), 0) < 0 {
                return Err(());
            }
            self.initialize_padding(&mut output)?;
            let source = f::av_frame_clone(self.input.0.as_ptr());
            if source.is_null() {
                return Err(());
            }
            let reference = f::av_buffer_create(
                source.cast(),
                size_of::<f::AVFrame>(),
                Some(release_source),
                source.cast(),
                0,
            );
            if reference.is_null() {
                release_source(source.cast(), ptr::null_mut());
                return Err(());
            }
            (*output.0.as_ptr()).opaque_ref = reference;
            let input = &*self.input.0.as_ptr();
            let destination = &*output.0.as_ptr();
            let mut region = f::VARectangle {
                x: 0,
                y: 0,
                width: input.width as u16,
                height: input.height as u16,
            };
            let mut placement = f::VARectangle {
                x: (((destination.width - input.width) / 2) & !1) as i16,
                y: (((destination.height - input.height) / 2) & !1) as i16,
                width: input.width as u16,
                height: input.height as u16,
            };
            let mut parameters = f::VAProcPipelineParameterBuffer {
                surface: input.data[3] as usize as u32,
                surface_region: &mut region,
                output_region: &mut placement,
                output_background_color: 0xff000000,
                surface_color_standard: f::VAProcColorStandardBT709,
                output_color_standard: f::VAProcColorStandardBT709,
                ..Default::default()
            };
            let filter = self.filter.as_ref().ok_or(())?;
            let mut buffer = 0;
            if f::vaCreateBuffer(
                filter.display,
                filter.context,
                f::VAProcPipelineParameterBufferType,
                size_of_val(&parameters) as u32,
                1,
                (&raw mut parameters).cast(),
                &mut buffer,
            ) != 0
            {
                return Err(());
            }
            let surface = destination.data[3] as usize as u32;
            let mut success = f::vaBeginPicture(filter.display, filter.context, surface) == 0;
            if success {
                let rendered =
                    f::vaRenderPicture(filter.display, filter.context, &mut buffer, 1) == 0;
                success = f::vaEndPicture(filter.display, filter.context) == 0 && rendered;
                if !success {
                    f::vaSyncSurface(filter.display, surface);
                }
            }
            f::vaDestroyBuffer(filter.display, buffer);
            if !success {
                return Err(());
            }
            self.input.clear();
            f::av_frame_move_ref(self.input.0.as_ptr(), output.0.as_ptr());
            Ok(())
        }
    }
    fn drain(&mut self, codec: u32, events: &mut Events) -> Result<()> {
        let _profile = Span::new(13);
        loop {
            let packet = self.packets.pop().map_or_else(Packet::new, Ok)?;
            // SAFETY: codec and exclusively owned empty packet are both live.
            let status =
                unsafe { f::avcodec_receive_packet(self.context.0.as_ptr(), packet.0.as_ptr()) };
            if status < 0 {
                self.packets.push(packet);
                return if status == -libc::EAGAIN || status == -541478725 {
                    Ok(())
                } else {
                    Err(())
                };
            }
            // SAFETY: successful receive initialized packet fields and owns size bytes.
            unsafe {
                let value = &mut *packet.0.as_ptr();
                let pending = self
                    .pending
                    .iter_mut()
                    .find(|entry| entry.is_some_and(|entry| entry.pts == value.pts))
                    .and_then(Option::take)
                    .ok_or(())?;
                if value.size <= 0 || value.data.is_null() {
                    return Err(());
                }
                let key = value.flags & f::AV_PKT_FLAG_KEY as i32 != 0;
                if codec == 2
                    && key
                    && (pending.crop.width != pending.meta.width
                        || pending.crop.height != pending.meta.height)
                {
                    let metadata = pending.crop.metadata().ok_or(())?;
                    let bytes = std::slice::from_raw_parts(value.data, value.size as usize);
                    let offset = insertion_position(bytes).ok_or(())?;
                    let size = value.size as usize;
                    if size > i32::MAX as usize - metadata.len()
                        || f::av_grow_packet(packet.0.as_ptr(), metadata.len() as i32) < 0
                    {
                        return Err(());
                    }
                    let value = &mut *packet.0.as_ptr();
                    ptr::copy(
                        value.data.add(offset),
                        value.data.add(offset + metadata.len()),
                        size - offset,
                    );
                    ptr::copy_nonoverlapping(
                        metadata.as_ptr(),
                        value.data.add(offset),
                        metadata.len(),
                    );
                }
                events.push(Event::Encoded {
                    meta: pending.meta,
                    packet,
                    info: (u64::from(u32::MAX) << 32) | if key { 2 } else { 1 },
                })?;
            }
        }
    }
    fn send(
        &mut self,
        codec: u32,
        meta: f::encoder_frame_meta,
        info: f::encoder_frame_info,
        pts: i64,
        events: &mut Events,
    ) -> Result<()> {
        if self.filter().is_err() {
            self.input.clear();
            return Err(());
        }
        let Some(index) = self.pending.iter().position(Option::is_none) else {
            self.input.clear();
            return Err(());
        };
        // SAFETY: checked input geometry fits this codec; the pending record is
        // installed before draining, so timestamp-to-metadata association is exact.
        unsafe {
            let context = &*self.context.0.as_ptr();
            let crop = Crop {
                x: ((context.width as u32 - meta.width) / 2) & !1,
                y: ((context.height as u32 - meta.height) / 2) & !1,
                width: meta.width,
                height: meta.height,
            };
            self.pending[index] = Some(Pending {
                pts,
                meta: f::encoder_frame_meta {
                    width: self.encoded.width,
                    height: self.encoded.height,
                    ..meta
                },
                crop,
            });
            let input = &mut *self.input.0.as_ptr();
            input.pts = pts;
            input.pict_type = if info.idr != 0 {
                f::AV_PICTURE_TYPE_I
            } else {
                f::AV_PICTURE_TYPE_NONE
            };
            let mut status;
            {
                let _profile = Span::new(12);
                status = f::avcodec_send_frame(self.context.0.as_ptr(), self.input.0.as_ptr());
            }
            if status == -libc::EAGAIN {
                if self.drain(codec, events).is_err() {
                    self.pending[index] = None;
                    self.input.clear();
                    return Err(());
                }
                let _profile = Span::new(12);
                status = f::avcodec_send_frame(self.context.0.as_ptr(), self.input.0.as_ptr());
            }
            if status < 0 && self.filter.is_some() {
                f::vaSyncSurface(
                    self.device.display(),
                    (*self.input.0.as_ptr()).data[3] as usize as u32,
                );
            }
            self.input.clear();
            if status < 0 {
                self.pending[index] = None;
                return Err(());
            }
        }
        self.drain(codec, events)
    }
    fn i420(&mut self, planes: &[f::encoder_plane; 3]) -> Result<()> {
        let width = self.opened.width as usize;
        let height = self.opened.height as usize;
        for (index, plane) in planes.iter().enumerate() {
            let divisor = if index == 0 { 1 } else { 2 };
            if plane.data.is_null()
                || plane.width < width / divisor
                || plane.height < height / divisor
                || plane.stride < width / divisor
                || plane.stride > i32::MAX as usize
                || (height / divisor)
                    .checked_mul(plane.stride)
                    .is_none_or(|size| size > isize::MAX as usize)
            {
                return Err(());
            }
        }
        // SAFETY: caller borrows the validated plane extents for this call.
        // FFmpeg owns destination allocations; each row stays inside its stride.
        unsafe {
            if self.cpu.is_none() {
                let cpu = Frame::new()?;
                let value = &mut *cpu.0.as_ptr();
                value.format = f::AV_PIX_FMT_NV12;
                value.width = width as i32;
                value.height = height as i32;
                if f::av_frame_get_buffer(cpu.0.as_ptr(), 32) < 0 {
                    return Err(());
                }
                self.cpu = Some(cpu);
            }
            let cpu = self.cpu.as_ref().ok_or(())?;
            if f::av_frame_make_writable(cpu.0.as_ptr()) < 0 {
                return Err(());
            }
            let value = &mut *cpu.0.as_ptr();
            if value.linesize[0] < width as i32
                || value.linesize[1] < width as i32
                || value.data[0].is_null()
                || value.data[1].is_null()
            {
                return Err(());
            }
            for row in 0..height {
                ptr::copy_nonoverlapping(
                    planes[0].data.add(row * planes[0].stride),
                    value.data[0].add(row * value.linesize[0] as usize),
                    width,
                );
            }
            for row in 0..height / 2 {
                let output = std::slice::from_raw_parts_mut(
                    value.data[1].add(row * value.linesize[1] as usize),
                    width,
                );
                let u = std::slice::from_raw_parts(
                    planes[1].data.add(row * planes[1].stride),
                    width / 2,
                );
                let v = std::slice::from_raw_parts(
                    planes[2].data.add(row * planes[2].stride),
                    width / 2,
                );
                for ((pair, u), v) in output.as_chunks_mut::<2>().0.iter_mut().zip(u).zip(v) {
                    pair[0] = *u;
                    pair[1] = *v;
                }
            }
            self.input.clear();
            let pool = self.upload.as_ref().unwrap_or(&self.frames);
            if f::av_hwframe_get_buffer(pool.0.as_ptr(), self.input.0.as_ptr(), 0) < 0
                || f::av_hwframe_transfer_data(self.input.0.as_ptr(), cpu.0.as_ptr(), 0) < 0
            {
                self.input.clear();
                return Err(());
            }
            Ok(())
        }
    }
    fn nv12(&mut self, input: &f::encoder_nv12, producer: ProducerLease) -> Result<()> {
        if input.width != self.opened.width
            || input.height != self.opened.height
            || input.fd < 0
            || input.size == 0
        {
            return Err(());
        }
        for plane in 0..2 {
            let rows = if plane == 0 {
                input.height
            } else {
                input.height / 2
            };
            let end = u64::from(input.offset[plane])
                + u64::from(rows - 1) * u64::from(input.stride[plane])
                + u64::from(input.width);
            if input.stride[plane] < input.width
                || input.offset[plane] >= input.size
                || end > u64::from(input.size)
            {
                return Err(());
            }
        }
        // SAFETY: descriptor planes were validated above; import retains a
        // duplicate DMA-BUF fd and the producer lease until last AVFrame release.
        unsafe {
            if self.imports.is_none() {
                let device = self.device.reference()?;
                let cache = f::dpe_import_cache_new(
                    device.0.as_ptr().cast(),
                    Some(import_create),
                    Some(import_destroy),
                    Some(import_finish),
                );
                if cache.is_null() {
                    return Err(());
                }
                device.into_raw();
                self.imports = Some(Imports(cache));
            }
            let entry;
            {
                let _profile = Span::new(10);
                entry = f::dpe_import_get(self.imports.as_ref().ok_or(())?.0, input);
            }
            let mut surface = 0usize;
            let imported = if !entry.is_null() {
                let imported = Imported::Cached(entry);
                if !f::dpe_import_surface(entry, &mut surface) {
                    return Err(());
                }
                imported
            } else if matches!(*libc::__errno_location(), libc::EAGAIN | libc::ENOTSUP) {
                let device = self.device.reference()?;
                if !import_create(device.0.as_ptr().cast(), input, &mut surface) {
                    return Err(());
                }
                TRANSIENT.fetch_add(1, Ordering::Relaxed);
                Imported::Transient {
                    device,
                    surface: surface as u32,
                }
            } else {
                return Err(());
            };
            self.input.clear();
            let frame = &mut *self.input.0.as_ptr();
            frame.format = f::AV_PIX_FMT_VAAPI;
            frame.width = input.width as i32;
            frame.height = input.height as i32;
            frame.data[3] = surface as *mut u8;
            frame.hw_frames_ctx = self
                .upload
                .as_ref()
                .unwrap_or(&self.frames)
                .reference()?
                .into_raw();
            let owner = Box::into_raw(Box::new(SurfaceLease {
                _surface: imported,
                _producer: producer,
            }));
            frame.buf[0] = f::av_buffer_create(
                owner.cast(),
                size_of::<SurfaceLease>(),
                Some(release_surface),
                owner.cast(),
                0,
            );
            if frame.buf[0].is_null() {
                release_surface(owner.cast(), ptr::null_mut());
                self.input.clear();
                return Err(());
            }
            Ok(())
        }
    }
    fn pool(device: &Buffer, size: f::encoder_dimensions) -> Result<Buffer> {
        // SAFETY: device remains owned; a failed initialization drops the newly allocated reference.
        unsafe {
            let pool = Buffer::adopt(f::av_hwframe_ctx_alloc(device.0.as_ptr()))?;
            let frames = &mut *(*pool.0.as_ptr()).data.cast::<f::AVHWFramesContext>();
            frames.format = f::AV_PIX_FMT_VAAPI;
            frames.sw_format = f::AV_PIX_FMT_NV12;
            frames.width = size.width as i32;
            frames.height = size.height as i32;
            if f::av_hwframe_ctx_init(pool.0.as_ptr()) < 0 {
                return Err(());
            }
            Ok(pool)
        }
    }
    fn open(
        node: &CStr,
        session: f::encoder_session,
        options: f::encoder_options,
        generation: u64,
    ) -> Result<(Self, u32, bool)> {
        if !valid(options) || options.fps == 0 || options.bitrate_target == 0 {
            return Err(());
        }
        // SAFETY: all foreign records belong to this partial construction. Owned
        // wrappers unwind in dependency order on every failure before publication.
        unsafe {
            let mut device = ptr::null_mut();
            if f::av_hwdevice_ctx_create(
                &mut device,
                f::AV_HWDEVICE_TYPE_VAAPI,
                node.as_ptr(),
                ptr::null_mut(),
                0,
            ) < 0
            {
                return Err(());
            }
            let device = Buffer::adopt(device)?;
            let driver = f::vaQueryVendorString(device.display());
            let driver = if driver.is_null() {
                &[][..]
            } else {
                CStr::from_ptr(driver).to_bytes()
            };
            let contains = |needle: &[u8]| driver.windows(needle.len()).any(|part| part == needle);
            let vendor = if contains(b"AMD") || contains(b"ATI") {
                1
            } else if contains(b"Intel") || contains(b"iHD") || contains(b"i965") {
                2
            } else {
                return Err(());
            };
            let mesa = contains(b"Mesa Gallium driver ");
            let vcn4 = session.codec == 2 && vendor == 1 && mesa && device_vcn4(node);
            let short =
                vcn4 && options.dimensions.width & 63 == 0 && options.dimensions.height & 15 == 8;
            let mut coded = options.dimensions;
            if session.codec == 2 && vendor == 1 && !short {
                coded.width = (coded.width + 63) & !63;
                coded.height = (coded.height + 15) & !15;
            }
            let mut encoded = coded;
            if short {
                encoded.height += 2;
            }
            let frames = Self::pool(&device, coded)?;
            let name = match session.codec {
                0 => c"h264_vaapi",
                1 => c"hevc_vaapi",
                2 => c"av1_vaapi",
                _ => return Err(()),
            };
            let codec = f::avcodec_find_encoder_by_name(name.as_ptr());
            if codec.is_null() {
                return Err(());
            }
            let context = Context(NonNull::new(f::avcodec_alloc_context3(codec)).ok_or(())?);
            let ctx = &mut *context.0.as_ptr();
            ctx.width = coded.width as i32;
            ctx.height = coded.height as i32;
            ctx.pix_fmt = f::AV_PIX_FMT_VAAPI;
            ctx.hw_frames_ctx = frames.reference()?.into_raw();
            ctx.time_base = f::AVRational { num: 1, den: 1000 };
            ctx.framerate = f::AVRational {
                num: options.fps as i32,
                den: 1,
            };
            ctx.bit_rate = i64::from(options.bitrate_target);
            ctx.rc_max_rate = i64::from(options.bitrate_maximum);
            ctx.rc_buffer_size = options.bitrate_target as i32;
            ctx.gop_size = if session.keyframe_interval == 0 {
                6000
            } else {
                session.keyframe_interval as i32
            };
            ctx.max_b_frames = 0;
            if session.codec == 0 {
                ctx.profile = f::AV_PROFILE_H264_CONSTRAINED_BASELINE as i32;
                ctx.color_range = f::AVCOL_RANGE_MPEG;
                ctx.colorspace = f::AVCOL_SPC_BT709;
                ctx.color_primaries = f::AVCOL_PRI_BT709;
                ctx.color_trc = f::AVCOL_TRC_BT709;
            }
            ctx.level = stream_level(session, options, vendor);
            if ctx.level == 0 && session.codec != 2 {
                return Err(());
            }
            ctx.flags |= f::AV_CODEC_FLAG_LOW_DELAY as i32;
            if f::av_opt_set(ctx.priv_data, c"rc_mode".as_ptr(), c"VBR".as_ptr(), 0) < 0
                || f::av_opt_set_int(ctx.priv_data, c"async_depth".as_ptr(), 1, 0) < 0
            {
                return Err(());
            }
            if vendor == 1 && mesa {
                ctx.compression_level = 0x13;
            }
            if f::avcodec_open2(context.0.as_ptr(), codec, ptr::null_mut()) < 0 {
                return Err(());
            }
            let input = Frame::new()?;
            let mut packets = Vec::with_capacity(PENDING);
            for _ in 0..PENDING {
                packets.push(Packet::new()?);
            }
            let mut result = Self {
                context,
                input,
                cpu: None,
                black: None,
                frames,
                upload: None,
                imports: None,
                filter: None,
                device,
                packets,
                initialized: Vec::with_capacity(8),
                pending: [None; PENDING],
                opened: options.dimensions,
                encoded,
                generation,
            };
            if coded.width != options.dimensions.width || coded.height != options.dimensions.height
            {
                result.upload = Some(Self::pool(&result.device, options.dimensions)?);
                let display = result.device.display();
                let mut config = 0;
                let mut context = 0;
                if f::vaCreateConfig(
                    display,
                    f::VAProfileNone,
                    f::VAEntrypointVideoProc,
                    ptr::null_mut(),
                    0,
                    &mut config,
                ) != 0
                {
                    return Err(());
                }
                if f::vaCreateContext(
                    display,
                    config,
                    coded.width as i32,
                    coded.height as i32,
                    f::VA_PROGRESSIVE as i32,
                    ptr::null_mut(),
                    0,
                    &mut context,
                ) != 0
                {
                    f::vaDestroyConfig(display, config);
                    return Err(());
                }
                result.filter = Some(Filter {
                    display,
                    config,
                    context,
                });
            }
            Ok((result, vendor, vcn4))
        }
    }
}
fn valid(options: f::encoder_options) -> bool {
    policy::options(
        options.dimensions.width,
        options.dimensions.height,
        options.fps,
        options.bitrate_target,
        options.bitrate_maximum,
    )
}
fn device_vcn4(node: &CStr) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(metadata) = std::fs::metadata(std::ffi::OsStr::from_bytes(node.to_bytes())) else {
        return false;
    };
    if !metadata.file_type().is_char_device() {
        return false;
    }
    let device = metadata.rdev();
    let path = format!(
        "/sys/dev/char/{}:{}/device/ip_discovery/die",
        libc::major(device),
        libc::minor(device)
    );
    let Ok(dies) = std::fs::read_dir(path) else {
        return false;
    };
    let mut found = false;
    for die in dies.take(32) {
        let Ok(die) = die else {
            return false;
        };
        let Ok(instances) = std::fs::read_dir(die.path().join("UVD")) else {
            continue;
        };
        for instance in instances.take(32) {
            let Ok(instance) = instance else {
                return false;
            };
            let Ok(version) = std::fs::read_to_string(instance.path().join("major")) else {
                return false;
            };
            if version.trim().parse::<u32>() != Ok(4) {
                return false;
            }
            found = true;
        }
    }
    found
}
fn stream_level(session: f::encoder_session, options: f::encoder_options, vendor: u32) -> i32 {
    let mut width = session.maximum.width.max(options.dimensions.width);
    let mut height = session.maximum.height.max(options.dimensions.height);
    let fps = session.fps.max(options.fps);
    match session.codec {
        0 => {
            // SAFETY: a pure FFmpeg table lookup returns static data or null.
            unsafe {
                f::ff_h264_guess_level(
                    f::AV_PROFILE_H264_CONSTRAINED_BASELINE as i32,
                    i64::from(options.bitrate_maximum),
                    fps as i32,
                    width as i32,
                    height as i32,
                    1,
                )
                .as_ref()
                .map_or(0, |level| i32::from(level.level_idc))
            }
        }
        2 => {
            if vendor == 1 {
                width = width.saturating_add(63) & !63;
                height = height.saturating_add(15) & !15;
            }
            policy::av1_level(width, height, fps, u64::from(options.bitrate_maximum))
        }
        _ => policy::hevc_level(width, height, fps, u64::from(options.bitrate_maximum)),
    }
}

struct State {
    codec: Option<Codec>,
    node: CString,
    session: f::encoder_session,
    options: f::encoder_options,
    vendor: u32,
    vcn4: bool,
    last_pts: i64,
    generation: u64,
}
// SAFETY: every codec/VA call is serialized by Session.state. FFmpeg frame
// references released by its workers have independent reference-counted owners;
// no borrowed plane, packet slice or callback context lives inside State.
unsafe impl Send for State {}
impl State {
    fn retire(&mut self, events: &mut Events) {
        if let Some(mut codec) = self.codec.take() {
            for entry in &mut codec.pending {
                if let Some(pending) = entry.take() {
                    let _ = events.push(Event::Dropped(pending.meta));
                }
            }
            // Free native references before reporting the cancelled inputs.
            drop(codec);
        }
    }
    fn update(&mut self, options: f::encoder_options, events: &mut Events) -> Result<()> {
        if !valid(options) {
            return Err(());
        }
        if options.fps == 0 || options.bitrate_target == 0 {
            self.options = options;
            return Ok(());
        }
        let required = stream_level(self.session, options, self.vendor);
        if self.session.codec != 2 && required == 0 {
            return Err(());
        }
        let reopen = self.codec.as_ref().is_none_or(|codec| {
            // SAFETY: the session holds exclusive codec ownership during update.
            let level = unsafe { (*codec.context.0.as_ptr()).level };
            codec.opened.width != options.dimensions.width
                || codec.opened.height != options.dimensions.height
                || required > level
        });
        if reopen {
            let mut previous = self.options;
            if let Some(codec) = &self.codec {
                // SAFETY: context fields are the actual last active settings,
                // including those retained while options represent suspension.
                unsafe {
                    let context = &*codec.context.0.as_ptr();
                    previous.dimensions = codec.opened;
                    previous.fps = context.framerate.num as u32;
                    previous.bitrate_target = context.bit_rate as u32;
                    previous.bitrate_maximum = context.rc_max_rate as u32;
                }
            }
            self.retire(events);
            self.generation = self.generation.checked_add(1).ok_or(())?;
            match Codec::open(&self.node, self.session, options, self.generation) {
                Ok((codec, vendor, vcn4)) => {
                    self.codec = Some(codec);
                    self.options = options;
                    self.vendor = vendor;
                    self.vcn4 = vcn4;
                    Ok(())
                }
                Err(()) => {
                    if let Ok((codec, vendor, vcn4)) =
                        Codec::open(&self.node, self.session, previous, self.generation)
                    {
                        self.codec = Some(codec);
                        self.options = previous;
                        self.vendor = vendor;
                        self.vcn4 = vcn4;
                    }
                    Err(())
                }
            }
        } else {
            let changed = self.options.fps != options.fps
                || self.options.bitrate_target != options.bitrate_target
                || self.options.bitrate_maximum != options.bitrate_maximum;
            self.options = options;
            if !changed {
                return Ok(());
            }
            let codec = self.codec.as_mut().ok_or(())?;
            // SAFETY: private VAAPIEncodeContext layout is generated from the
            // exact checksum-verified, patched headers linked into this DSO.
            unsafe {
                let context = &mut *codec.context.0.as_ptr();
                context.bit_rate = i64::from(options.bitrate_target);
                context.rc_max_rate = i64::from(options.bitrate_maximum);
                context.rc_buffer_size = options.bitrate_target as i32;
                context.framerate = f::AVRational {
                    num: options.fps as i32,
                    den: 1,
                };
                let va = &mut *context.priv_data.cast::<f::VAAPIEncodeContext>();
                va.rc_params.bits_per_second = options.bitrate_maximum;
                va.rc_params.target_percentage = (u64::from(options.bitrate_target) * 100)
                    .div_ceil(u64::from(options.bitrate_maximum))
                    as u32;
                va.rc_params.window_size = (u64::from(options.bitrate_target) * 1000
                    / u64::from(options.bitrate_maximum))
                    as u32;
                va.hrd_params.buffer_size = options.bitrate_target;
                va.hrd_params.initial_buffer_fullness =
                    (u64::from(options.bitrate_target) * 3 / 4) as u32;
                va.fr_params.framerate = (1 << 16) | options.fps;
                va.dpe_rate_update_pending = 1;
            }
            Ok(())
        }
    }
    fn submit(
        &mut self,
        meta: f::encoder_frame_meta,
        info: f::encoder_frame_info,
        events: &mut Events,
        prepare: impl FnOnce(&mut Codec) -> Result<()>,
    ) -> Result<()> {
        if self.options.fps == 0 || self.options.bitrate_target == 0 {
            events.push(Event::Dropped(meta))?;
            return Ok(());
        }
        if meta.width != self.options.dimensions.width
            || meta.height != self.options.dimensions.height
        {
            return Err(());
        }
        let codec = self.codec.as_mut().ok_or(())?;
        prepare(codec)?;
        let Some(pts) = policy::timestamp(self.last_pts, info.timestamp_ms) else {
            codec.input.clear();
            return Err(());
        };
        self.last_pts = pts;
        codec.send(self.session.codec, meta, info, pts, events)
    }
}
#[derive(Clone, Copy)]
struct Callbacks {
    context: *mut c_void,
    encoded: unsafe extern "C" fn(
        *mut c_void,
        *const f::encoder_frame_meta,
        u64,
        *const u8,
        usize,
        *const c_void,
    ),
    dropped: unsafe extern "C" fn(*mut c_void, *const f::encoder_frame_meta),
}
// SAFETY: the C API supplies thread-capable callbacks and keeps context alive
// until destroy returns. Admission waits for all foreign operations/callbacks;
// a reentrant destroy cancels subsequent callbacks before returning.
unsafe impl Send for Callbacks {}
// SAFETY: callback invocation follows the same explicit admission contract.
unsafe impl Sync for Callbacks {}
struct Session {
    state: Mutex<Option<State>>,
    admission: Arc<Admission>,
    callbacks: Callbacks,
}
fn sessions() -> &'static Handles<Arc<Session>> {
    static TABLE: OnceLock<Handles<Arc<Session>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::pointers(128, PointerKind::Encoder))
}
fn invoke(session: &Session, event: &Event) {
    // SAFETY: the operation admission or the final destroy call owns context
    // lifetime, and every encoded packet stays owned across its callback.
    unsafe {
        match event {
            Event::Dropped(meta) => (session.callbacks.dropped)(session.callbacks.context, meta),
            Event::Encoded { meta, packet, info } => {
                let _profile = Span::new(14);
                let value = &*packet.0.as_ptr();
                (session.callbacks.encoded)(
                    session.callbacks.context,
                    meta,
                    *info,
                    value.data,
                    value.size as usize,
                    ptr::null(),
                );
            }
        }
    }
}
fn operation(
    handle: *mut c_void,
    action: impl FnOnce(&mut State, &mut Events) -> Result<()>,
) -> bool {
    let Some(session) = sessions().get(handle as usize) else {
        return false;
    };
    let Some(_operation) = session.admission.enter() else {
        return false;
    };
    let mut events = Events::default();
    let (result, generation) = {
        let mut guard = lock(&session.state);
        let Some(state) = guard.as_mut() else {
            return false;
        };
        let result = action(state, &mut events);
        (result, state.generation)
    };
    for event in events.items.into_iter().flatten() {
        if session.admission.open() {
            invoke(&session, &event);
        }
        if let Event::Encoded { mut packet, .. } = event {
            packet.clear();
            let mut guard = lock(&session.state);
            if let Some(codec) = guard.as_mut().and_then(|state| state.codec.as_mut())
                && codec.generation == generation
                && codec.packets.len() < PENDING
            {
                codec.packets.push(packet);
            }
        }
    }
    result.is_ok()
}
unsafe extern "C" fn create(
    node: *const libc::c_char,
    mut session: f::encoder_session,
    options: f::encoder_options,
    context: *mut c_void,
    encoded: f::encoder_encoded_fn,
    dropped: f::encoder_dropped_fn,
) -> *mut c_void {
    let (Some(encoded), Some(dropped)) = (encoded, dropped) else {
        return ptr::null_mut();
    };
    if node.is_null()
        || !valid(options)
        || session.codec > 2
        || session.keyframe_interval > i32::MAX as u32
        || session.fps > 240
        || session.maximum.width > 8192
        || session.maximum.height > 8192
    {
        return ptr::null_mut();
    }
    // SAFETY: create borrows a valid NUL-terminated node argument for this call.
    let node = unsafe { CStr::from_ptr(node) };
    if node.to_bytes().len() > 4096 {
        return ptr::null_mut();
    }
    let Ok((codec, vendor, vcn4)) = Codec::open(node, session, options, 1) else {
        return ptr::null_mut();
    };
    // No session callback retains this foreign experiment string.
    session.experiments = ptr::null();
    let owner = Arc::new(Session {
        state: Mutex::new(Some(State {
            codec: Some(codec),
            node: node.to_owned(),
            session,
            options,
            vendor,
            vcn4,
            last_pts: 0,
            generation: 1,
        })),
        admission: Arc::new(Admission::default()),
        callbacks: Callbacks {
            context,
            encoded,
            dropped,
        },
    });
    sessions()
        .insert(owner)
        .map_or(ptr::null_mut(), |token| token.get() as *mut c_void)
}
unsafe extern "C" fn update(handle: *mut c_void, options: f::encoder_options) -> bool {
    operation(handle, |state, events| state.update(options, events))
}
unsafe extern "C" fn destroy(handle: *mut c_void) {
    let Some(session) = sessions().remove(handle as usize) else {
        return;
    };
    let notify = session.admission.close();
    let mut events = Events::default();
    {
        let mut guard = lock(&session.state);
        if let Some(mut state) = guard.take() {
            state.retire(&mut events);
        }
    }
    if notify {
        for event in events.items.into_iter().flatten() {
            invoke(&session, &event);
        }
    }
}
unsafe extern "C" fn vendor(handle: *mut c_void) -> u32 {
    sessions()
        .get(handle as usize)
        .and_then(|session| lock(&session.state).as_ref().map(|state| state.vendor))
        .unwrap_or(4)
}
unsafe extern "C" fn i420(
    handle: *mut c_void,
    meta: *const f::encoder_frame_meta,
    info: f::encoder_frame_info,
    planes: *const f::encoder_plane,
) -> bool {
    if meta.is_null() || planes.is_null() {
        return false;
    }
    // SAFETY: C API borrows the complete metadata and three plane records until return.
    let (meta, planes) = unsafe { (*meta, &*planes.cast::<[f::encoder_plane; 3]>()) };
    operation(handle, |state, events| {
        state.submit(meta, info, events, |codec| codec.i420(planes))
    })
}
unsafe extern "C" fn nv12(
    handle: *mut c_void,
    meta: *const f::encoder_frame_meta,
    info: f::encoder_frame_info,
    input: *const f::encoder_nv12,
    lease: *mut c_void,
    release: Option<unsafe extern "C" fn(*mut c_void)>,
) -> bool {
    let Some(release) = release else {
        return false;
    };
    let producer = ProducerLease {
        value: lease,
        release,
    };
    if meta.is_null() || input.is_null() {
        return false;
    }
    // SAFETY: caller supplies these initialized records for the duration of this
    // call. ProducerLease is consumed exactly once on every admission/failure path.
    let (meta, input) = unsafe { (*meta, &*input) };
    operation(handle, |state, events| {
        state.submit(meta, info, events, |codec| codec.nv12(input, producer))
    })
}
static TRANSIENT: AtomicU64 = AtomicU64::new(0);
#[unsafe(no_mangle)]
pub extern "C" fn dpe_encoder_hardware_scope(handle: *mut c_void) -> u32 {
    sessions()
        .get(handle as usize)
        .and_then(|session| {
            lock(&session.state)
                .as_ref()
                .map(|state| u32::from(state.vcn4))
        })
        .unwrap_or(0)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_encoder_profile_stage(stage: u32, output: *mut u64) -> bool {
    // SAFETY: exported C ABI borrows a writable four-counter array.
    unsafe { dpe_os::profiler::dpe_profile_stage(stage, output) }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_encoder_import_stats(output: *mut u64) {
    if output.is_null() {
        return;
    }
    // SAFETY: C ABI supplies five writable counters; cache writes only the first four.
    unsafe {
        f::dpe_import_stats(output);
        output.add(4).write(TRANSIENT.load(Ordering::Relaxed));
    }
}
#[derive(Default)]
struct Probe {
    vps: bool,
    sps: bool,
    pps: bool,
    picture: bool,
}
unsafe extern "C" fn probe_encoded(
    context: *mut c_void,
    _: *const f::encoder_frame_meta,
    _: u64,
    data: *const u8,
    size: usize,
    _: *const c_void,
) {
    // SAFETY: this callback only serves the synchronous local probe. Its stack
    // owner and every packet remain live until callback completion.
    unsafe {
        let result = &mut *context.cast::<Probe>();
        let bytes = std::slice::from_raw_parts(data, size);
        for window in bytes.windows(6) {
            if window[..3] != [0, 0, 1] {
                continue;
            }
            let kind = (window[3] >> 1) & 63;
            result.vps |= kind == 32;
            result.sps |= kind == 33;
            result.pps |= kind == 34;
            result.picture |= kind == 19 || kind == 20;
        }
    }
}
unsafe extern "C" fn probe_dropped(_: *mut c_void, _: *const f::encoder_frame_meta) {}
unsafe extern "C" fn probe(node: *const libc::c_char) -> u32 {
    let mut result = Probe::default();
    let options = f::encoder_options {
        fps: 30,
        dimensions: f::encoder_dimensions {
            width: 256,
            height: 256,
        },
        bitrate_target: 500_000,
        bitrate_maximum: 1_000_000,
        keyframe_interval: 3000,
    };
    let session = f::encoder_session {
        fps: 30,
        maximum: options.dimensions,
        codec: 1,
        ..Default::default()
    };
    // SAFETY: all probe buffers/context live through encoder destruction; only
    // synthetic NV12 black is submitted and no image data is saved.
    unsafe {
        let handle = create(
            node,
            session,
            options,
            (&raw mut result).cast(),
            Some(probe_encoded),
            Some(probe_dropped),
        );
        if handle.is_null() {
            return 4;
        }
        let mut pixels = vec![128u8; 256 * 256 * 3 / 2];
        pixels[..65536].fill(16);
        let planes = [
            f::encoder_plane {
                data: pixels.as_ptr(),
                stride: 256,
                width: 256,
                height: 256,
            },
            f::encoder_plane {
                data: pixels.as_ptr().add(65536),
                stride: 128,
                width: 128,
                height: 128,
            },
            f::encoder_plane {
                data: pixels.as_ptr().add(81920),
                stride: 128,
                width: 128,
                height: 128,
            },
        ];
        let mut valid = true;
        for index in 0..3 {
            let meta = f::encoder_frame_meta {
                width: 256,
                height: 256,
                ..Default::default()
            };
            let info = f::encoder_frame_info {
                idr: u8::from(index == 0),
                timestamp_ms: index * 34,
                ..Default::default()
            };
            valid &= i420(handle, &meta, info, planes.as_ptr());
            if !valid {
                break;
            }
        }
        let selected = vendor(handle);
        destroy(handle);
        if valid && result.vps && result.sps && result.pps && result.picture {
            selected
        } else {
            4
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hevc_api() -> *const f::dpe_hevc_api {
    static API: f::dpe_hevc_api = f::dpe_hevc_api {
        abi: f::DPE_HEVC_ABI,
        size: size_of::<f::dpe_hevc_api>() as u32,
        probe: Some(probe),
        create: Some(create),
        update: Some(update),
        i420: Some(i420),
        nv12: Some(nv12),
        destroy: Some(destroy),
        vendor: Some(vendor),
    };
    &API
}
