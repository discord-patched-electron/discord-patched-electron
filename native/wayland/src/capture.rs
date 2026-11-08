// SPDX-License-Identifier: MIT
//! Dedicated output capture. Typed protocol events only update owned state;
//! consumers receive events after dispatch. A pending frame owns its buffer
//! and GPU storage until completion or connection teardown.
use std::{
    cell::Cell,
    collections::VecDeque,
    os::fd::{AsFd, AsRawFd, BorrowedFd},
    rc::Rc,
};
use wayland_client::{
    Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum, delegate_noop,
    protocol::{wl_buffer, wl_output, wl_registry, wl_shm, wl_shm_pool},
};
use wayland_protocols::{
    ext::{
        image_capture_source::v1::client::{
            ext_image_capture_source_v1 as source,
            ext_output_image_capture_source_manager_v1 as sources,
        },
        image_copy_capture::v1::client::{
            ext_image_copy_capture_frame_v1 as frame, ext_image_copy_capture_manager_v1 as manager,
            ext_image_copy_capture_session_v1 as session,
        },
    },
    wp::linux_dmabuf::zv1::client::{
        zwp_linux_buffer_params_v1 as params, zwp_linux_dmabuf_v1 as dmabuf,
    },
    xdg::xdg_output::zv1::client::{
        zxdg_output_manager_v1 as output_manager, zxdg_output_v1 as logical_output,
    },
};
type Result<T> = std::result::Result<T, i32>;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Format {
    pub format: u32,
    pub modifier: u64,
    pub dmabuf: bool,
}
#[derive(Clone, Debug, Default)]
pub struct Constraints {
    pub generation: u64,
    pub width: u32,
    pub height: u32,
    pub device: Option<libc::dev_t>,
    pub formats: Vec<Format>,
}
#[derive(Clone, Copy, Debug, Default)]
pub struct Frame {
    pub generation: u64,
    pub presentation_ns: u64,
    pub transform: u32,
    pub damage: Option<(i32, i32, i32, i32)>,
}
pub struct Plane<'a> {
    pub descriptor: BorrowedFd<'a>,
    pub offset: u32,
    pub stride: u32,
}
struct Image<T> {
    owner: Rc<()>,
    proxy: wl_buffer::WlBuffer,
    generation: Cell<u64>,
    size: (u32, u32),
    format: Format,
    storage: T,
}
impl<T> Drop for Image<T> {
    fn drop(&mut self) {
        self.proxy.destroy();
    }
}
pub struct Buffer<T>(Rc<Image<T>>);
impl<T> Clone for Buffer<T> {
    fn clone(&self) -> Self {
        Self(self.0.clone())
    }
}
impl<T> Buffer<T> {
    pub fn same_image(&self, other: &Self) -> bool {
        Rc::ptr_eq(&self.0, &other.0)
    }
    pub fn storage(&self) -> &T {
        &self.0.storage
    }
    pub fn generation(&self) -> u64 {
        self.0.generation.get()
    }
}
pub enum Event<T> {
    Constraints(Constraints),
    Frame {
        buffer: Buffer<T>,
        frame: Frame,
        status: i32,
    },
    Stopped(i32),
}
#[derive(Clone)]
pub struct OutputInfo {
    pub name: String,
    pub description: String,
    pub width: u32,
    pub height: u32,
    pub transform: u32,
    pub position: Option<(i32, i32)>,
}
struct Output {
    global: u32,
    proxy: wl_output::WlOutput,
    name: String,
    done: bool,
    info: OutputInfo,
    logical: Option<logical_output::ZxdgOutputV1>,
    logical_position: Option<(i32, i32)>,
}
struct Pending<T> {
    proxy: frame::ExtImageCopyCaptureFrameV1,
    buffer: Buffer<T>,
    frame: Frame,
    time: bool,
    transform: bool,
}
struct State<T> {
    registry: Option<wl_registry::WlRegistry>,
    outputs: Vec<Output>,
    selected: Option<u32>,
    sources: Option<sources::ExtOutputImageCaptureSourceManagerV1>,
    manager: Option<manager::ExtImageCopyCaptureManagerV1>,
    source: Option<source::ExtImageCaptureSourceV1>,
    session: Option<session::ExtImageCopyCaptureSessionV1>,
    dmabuf: Option<dmabuf::ZwpLinuxDmabufV1>,
    shm: Option<wl_shm::WlShm>,
    output_manager: Option<output_manager::ZxdgOutputManagerV1>,
    current: Constraints,
    batch: Option<Constraints>,
    has_size: bool,
    rejected: u64,
    pending: Option<Pending<T>>,
    events: VecDeque<Event<T>>,
    error: Option<i32>,
    reported: bool,
    accept: fn(u32, bool) -> bool,
}
impl<T> State<T> {
    fn new(accept: fn(u32, bool) -> bool) -> Self {
        Self {
            registry: None,
            outputs: Vec::with_capacity(32),
            selected: None,
            sources: None,
            manager: None,
            source: None,
            session: None,
            dmabuf: None,
            shm: None,
            output_manager: None,
            current: Constraints::default(),
            batch: None,
            has_size: false,
            rejected: 0,
            pending: None,
            events: VecDeque::with_capacity(16),
            error: None,
            reported: false,
            accept,
        }
    }
    fn stop(&mut self, error: i32) {
        if self.error.is_none() {
            self.error = Some(error);
        }
    }
    fn push(&mut self, event: Event<T>) {
        if self.events.len() >= 32 {
            self.stop(-libc::EOVERFLOW);
        } else {
            self.events.push_back(event);
        }
    }
    fn batch(&mut self) -> &mut Constraints {
        if self.batch.is_none() {
            self.has_size = false;
            self.batch = Some(Constraints {
                formats: Vec::with_capacity(16),
                ..Default::default()
            });
        }
        self.batch.as_mut().unwrap()
    }
    fn format(&mut self, value: Format) {
        if !(self.accept)(value.format, value.dmabuf) {
            return;
        }
        let batch = self.batch();
        if batch.formats.contains(&value) {
            return;
        }
        if batch.formats.len() == 256 {
            self.stop(-libc::EOVERFLOW);
        } else {
            batch.formats.push(value);
        }
    }
    fn finish(&mut self, status: i32) {
        if let Some(pending) = self.pending.take() {
            pending.proxy.destroy();
            self.push(Event::Frame {
                buffer: pending.buffer,
                frame: pending.frame,
                status: self.error.unwrap_or(status),
            });
        } else {
            self.stop(-libc::EPROTO);
        }
    }
}
impl<T: 'static> State<T> {
    fn bind_logical(&mut self, qh: &QueueHandle<Self>) {
        if let Some(manager) = &self.output_manager {
            for output in &mut self.outputs {
                if output.logical.is_none() {
                    output.logical = Some(manager.get_xdg_output(&output.proxy, qh, output.global));
                }
            }
        }
    }
}
impl<T> Drop for State<T> {
    fn drop(&mut self) {
        // Destroy native frame/session before releasing outstanding GPU storage.
        if let Some(pending) = &self.pending {
            pending.proxy.destroy();
        }
        if let Some(session) = self.session.take() {
            session.destroy();
        }
        if let Some(source) = self.source.take() {
            source.destroy();
        }
        if let Some(sources) = self.sources.take() {
            sources.destroy();
        }
        if let Some(manager) = self.manager.take() {
            manager.destroy();
        }
        if let Some(dmabuf) = self.dmabuf.take() {
            dmabuf.destroy();
        }
        for output in self.outputs.drain(..) {
            if let Some(logical) = output.logical {
                logical.destroy();
            }
            output.proxy.release();
        }
        if let Some(manager) = self.output_manager.take() {
            manager.destroy();
        }
        drop(self.registry.take());
    }
}
pub struct Capture<T: 'static> {
    owner: Rc<()>,
    write_blocked: Cell<bool>,
    state: State<T>,
    queue: EventQueue<State<T>>,
    connection: Connection,
}
impl<T: 'static> Capture<T> {
    pub fn connect(accept: fn(u32, bool) -> bool) -> Result<Self> {
        let connection = Connection::connect_to_env().map_err(|_| -libc::ENOTCONN)?;
        Self::from_connection(connection, accept)
    }
    pub fn from_connection(connection: Connection, accept: fn(u32, bool) -> bool) -> Result<Self> {
        let mut queue = connection.new_event_queue();
        let qh = queue.handle();
        let mut state = State::new(accept);
        state.registry = Some(connection.display().get_registry(&qh, ()));
        queue.roundtrip(&mut state).map_err(|_| -libc::EPIPE)?;
        queue.roundtrip(&mut state).map_err(|_| -libc::EPIPE)?;
        if let Some(error) = state.error {
            return Err(error);
        }
        Ok(Self {
            owner: Rc::new(()),
            write_blocked: Cell::new(false),
            state,
            queue,
            connection,
        })
    }
    pub fn outputs(&self) -> Vec<String> {
        self.state
            .outputs
            .iter()
            .filter(|o| o.done && !o.name.is_empty())
            .map(|o| o.name.clone())
            .collect()
    }
    pub fn inventory(&self) -> Vec<OutputInfo> {
        self.state
            .outputs
            .iter()
            .filter(|output| output.done && !output.name.is_empty())
            .map(|output| {
                let mut info = output.info.clone();
                info.name = output.name.clone();
                if let Some(position) = output.logical_position {
                    info.position = Some(position);
                }
                info
            })
            .collect()
    }
    pub fn dma_supported(&self) -> bool {
        self.supported() && self.state.dmabuf.is_some()
    }
    pub fn supported(&self) -> bool {
        self.state.sources.is_some()
            && self.state.manager.is_some()
            && (self.state.dmabuf.is_some() || self.state.shm.is_some())
    }
    /// Blocking control barrier for startup and private protocol fixtures.
    /// Frame production uses descriptor polling and nonblocking dispatch.
    pub fn roundtrip(&mut self) -> Result<()> {
        self.queue
            .roundtrip(&mut self.state)
            .map(|_| ())
            .map_err(|_| -libc::EPIPE)
    }
    pub fn allocate_shm(
        &self,
        constraints: &Constraints,
        format: Format,
        descriptor: BorrowedFd<'_>,
        stride: u32,
        storage: T,
    ) -> Result<Buffer<T>> {
        if let Some(error) = self.state.error {
            return Err(error);
        }
        let bytes = stride
            .checked_mul(constraints.height)
            .filter(|size| *size <= i32::MAX as u32)
            .ok_or(-libc::EINVAL)?;
        if format.dmabuf
            || format.modifier != 0
            || constraints.generation == 0
            || constraints.width == 0
            || constraints.height == 0
            || constraints.width > 16384
            || constraints.height > 16384
            || stride < constraints.width * 4
            || !constraints.formats.contains(&format)
        {
            return Err(-libc::EINVAL);
        }
        let shm = self.state.shm.as_ref().ok_or(-libc::ENOTSUP)?;
        let pixel = wl_shm::Format::try_from(format.format).map_err(|_| -libc::ENOTSUP)?;
        let qh = self.queue.handle();
        let pool = shm.create_pool(descriptor, bytes as i32, &qh, ());
        let proxy = pool.create_buffer(
            0,
            constraints.width as i32,
            constraints.height as i32,
            stride as i32,
            pixel,
            &qh,
            (),
        );
        pool.destroy();
        Ok(Buffer(Rc::new(Image {
            owner: self.owner.clone(),
            proxy,
            generation: Cell::new(constraints.generation),
            size: (constraints.width, constraints.height),
            format,
            storage,
        })))
    }
    pub fn start(&mut self, name: &str, paint_cursor: bool) -> Result<()> {
        if let Some(error) = self.state.error {
            return Err(error);
        }
        if self.state.session.is_some() {
            return Err(-libc::EALREADY);
        }
        if !self.supported() {
            return Err(-libc::ENOTSUP);
        }
        if name.is_empty() || name.len() >= 128 {
            return Err(-libc::EINVAL);
        }
        let mut matching = self
            .state
            .outputs
            .iter()
            .filter(|o| o.done && o.name == name);
        let output = matching.next().ok_or(-libc::ENOENT)?;
        if matching.next().is_some() {
            return Err(-libc::EEXIST);
        }
        let qh = self.queue.handle();
        self.state.selected = Some(output.global);
        let source = self
            .state
            .sources
            .as_ref()
            .unwrap()
            .create_source(&output.proxy, &qh, ());
        self.state.session = Some(self.state.manager.as_ref().unwrap().create_session(
            &source,
            if paint_cursor {
                manager::Options::PaintCursors
            } else {
                manager::Options::empty()
            },
            &qh,
            (),
        ));
        self.state.source = Some(source);
        self.flush()
    }
    pub fn descriptor(&self) -> BorrowedFd<'_> {
        self.connection.as_fd()
    }
    pub fn flush(&self) -> Result<()> {
        match self.connection.flush() {
            Ok(()) => {
                self.write_blocked.set(false);
                Ok(())
            }
            Err(wayland_client::backend::WaylandError::Io(error))
                if error.kind() == std::io::ErrorKind::WouldBlock =>
            {
                self.write_blocked.set(true);
                Ok(())
            }
            Err(_) => Err(-libc::EPIPE),
        }
    }
    pub fn needs_flush(&self) -> bool {
        self.write_blocked.get()
    }
    pub fn dispatch(&mut self) -> Result<()> {
        self.queue
            .dispatch_pending(&mut self.state)
            .map_err(|_| -libc::EPIPE)?;
        if let Some(guard) = self.queue.prepare_read() {
            match guard.read() {
                Ok(_) => (),
                Err(wayland_client::backend::WaylandError::Io(error))
                    if error.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(_) => return Err(-libc::EPIPE),
            }
        }
        self.queue
            .dispatch_pending(&mut self.state)
            .map_err(|_| -libc::EPIPE)?;
        self.flush()
    }
    pub fn next_event(&mut self) -> Option<Event<T>> {
        if let Some(error) = self.state.error
            && !self.state.reported
        {
            self.state.reported = true;
            return Some(Event::Stopped(error));
        }
        self.state.events.pop_front()
    }
    pub fn allocate(
        &self,
        constraints: &Constraints,
        format: Format,
        planes: &[Plane<'_>],
        storage: T,
    ) -> Result<Buffer<T>> {
        if let Some(error) = self.state.error {
            return Err(error);
        }
        if !format.dmabuf
            || format.modifier == u64::MAX
            || !(1..=4).contains(&planes.len())
            || constraints.generation == 0
            || constraints.width == 0
            || constraints.height == 0
            || constraints.width > i32::MAX as u32
            || constraints.height > i32::MAX as u32
            || !constraints.formats.contains(&format)
            || planes.iter().any(|p| p.stride == 0)
        {
            return Err(-libc::EINVAL);
        }
        let dmabuf = self.state.dmabuf.as_ref().ok_or(-libc::ENOTSUP)?;
        let qh = self.queue.handle();
        let params = dmabuf.create_params(&qh, ());
        for (index, plane) in planes.iter().enumerate() {
            params.add(
                plane.descriptor,
                index as u32,
                plane.offset,
                plane.stride,
                (format.modifier >> 32) as u32,
                format.modifier as u32,
            );
        }
        let proxy = params.create_immed(
            constraints.width as i32,
            constraints.height as i32,
            format.format,
            params::Flags::empty(),
            &qh,
            (),
        );
        params.destroy();
        Ok(Buffer(Rc::new(Image {
            owner: self.owner.clone(),
            proxy,
            generation: Cell::new(constraints.generation),
            size: (constraints.width, constraints.height),
            format,
            storage,
        })))
    }
    pub fn submit(&mut self, buffer: Buffer<T>) -> std::result::Result<(), (i32, Buffer<T>)> {
        let check = || -> Result<()> {
            if !Rc::ptr_eq(&self.owner, &buffer.0.owner) {
                return Err(-libc::EINVAL);
            }
            if let Some(error) = self.state.error {
                return Err(error);
            }
            if self.state.pending.is_some() {
                return Err(-libc::EBUSY);
            }
            if self.state.session.is_none()
                || self.state.current.generation == 0
                || self.state.batch.is_some()
            {
                return Err(-libc::EAGAIN);
            }
            if buffer.generation() != self.state.current.generation
                || buffer.generation() <= self.state.rejected
                || buffer.0.size != (self.state.current.width, self.state.current.height)
            {
                return Err(-libc::ESTALE);
            }
            if !self.state.current.formats.contains(&buffer.0.format) {
                return Err(-libc::ENOTSUP);
            }
            Ok(())
        };
        if let Err(error) = check() {
            return Err((error, buffer));
        }
        let proxy = self
            .state
            .session
            .as_ref()
            .unwrap()
            .create_frame(&self.queue.handle(), ());
        proxy.attach_buffer(&buffer.0.proxy);
        proxy.damage_buffer(0, 0, buffer.0.size.0 as i32, buffer.0.size.1 as i32);
        proxy.capture();
        self.state.pending = Some(Pending {
            proxy,
            frame: Frame {
                generation: buffer.generation(),
                ..Default::default()
            },
            buffer,
            time: false,
            transform: false,
        });
        // Submission owns the storage even if flushing fails. Teardown then
        // disconnects its dedicated connection before the consumer drops it.
        if let Err(error) = self.flush() {
            self.state.stop(error);
        }
        Ok(())
    }
    /// Reuse an existing compatible destination after a completed constraints
    /// batch. An outstanding compositor write can never be retagged.
    pub fn refresh(&mut self, buffer: &Buffer<T>) -> Result<()> {
        if let Some(error) = self.state.error {
            return Err(error);
        }
        if !Rc::ptr_eq(&self.owner, &buffer.0.owner) {
            return Err(-libc::EINVAL);
        }
        if self
            .state
            .pending
            .as_ref()
            .is_some_and(|pending| Rc::ptr_eq(&pending.buffer.0, &buffer.0))
        {
            return Err(-libc::EBUSY);
        }
        if self.state.batch.is_some() {
            return Err(-libc::EAGAIN);
        }
        if buffer.0.size != (self.state.current.width, self.state.current.height)
            || !self.state.current.formats.contains(&buffer.0.format)
            || self.state.current.generation <= self.state.rejected
        {
            return Err(-libc::ESTALE);
        }
        buffer.0.generation.set(self.state.current.generation);
        Ok(())
    }
    pub fn disconnect(&mut self) {
        self.state.stop(-libc::ECANCELED);
        // SAFETY: only this dedicated owned socket is shut down, before any
        // pending compositor destination or its GPU storage is retired.
        unsafe {
            libc::shutdown(self.connection.as_fd().as_raw_fd(), libc::SHUT_RDWR);
        }
    }
}
impl<T: 'static> Dispatch<wl_registry::WlRegistry, ()> for State<T> {
    fn event(
        state: &mut Self,
        registry: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            wl_registry::Event::Global {
                name,
                interface,
                version,
            } => {
                if interface == manager::ExtImageCopyCaptureManagerV1::interface().name
                    && state.manager.is_none()
                {
                    state.manager = Some(registry.bind(name, 1, qh, ()));
                } else if interface
                    == sources::ExtOutputImageCaptureSourceManagerV1::interface().name
                    && state.sources.is_none()
                {
                    state.sources = Some(registry.bind(name, 1, qh, ()));
                } else if interface == dmabuf::ZwpLinuxDmabufV1::interface().name
                    && version >= 3
                    && state.dmabuf.is_none()
                {
                    state.dmabuf = Some(registry.bind(name, 3, qh, ()));
                } else if interface == "wl_shm" && state.shm.is_none() {
                    state.shm = Some(registry.bind(name, 1, qh, ()));
                } else if interface == output_manager::ZxdgOutputManagerV1::interface().name
                    && state.output_manager.is_none()
                {
                    state.output_manager = Some(registry.bind(name, version.min(3), qh, ()));
                } else if interface == "wl_output" && version >= 4 {
                    if state.outputs.len() >= 32 {
                        state.stop(-libc::EOVERFLOW);
                    } else {
                        state.outputs.push(Output {
                            global: name,
                            proxy: registry.bind(name, 4, qh, name),
                            name: String::new(),
                            done: false,
                            info: OutputInfo {
                                name: String::new(),
                                description: String::new(),
                                width: 0,
                                height: 0,
                                transform: 0,
                                position: None,
                            },
                            logical: None,
                            logical_position: None,
                        });
                    }
                }
            }
            wl_registry::Event::GlobalRemove { name } => {
                if let Some(index) = state.outputs.iter().position(|o| o.global == name) {
                    if state.selected == Some(name) {
                        state.stop(-libc::ENODEV);
                    }
                    let output = state.outputs.remove(index);
                    if let Some(logical) = output.logical {
                        logical.destroy();
                    }
                    output.proxy.release();
                }
            }
            _ => (),
        }
        state.bind_logical(qh);
    }
}
impl<T: 'static> Dispatch<wl_output::WlOutput, u32> for State<T> {
    fn event(
        state: &mut Self,
        _: &wl_output::WlOutput,
        event: wl_output::Event,
        global: &u32,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        let Some(output) = state.outputs.iter_mut().find(|o| o.global == *global) else {
            return;
        };
        match event {
            wl_output::Event::Name { name } => {
                if name.is_empty() || name.len() >= 128 || !output.name.is_empty() {
                    state.stop(-libc::EPROTO);
                } else {
                    output.name = name;
                }
            }
            wl_output::Event::Done => output.done = true,
            wl_output::Event::Geometry {
                x, y, transform, ..
            } => {
                output.info.position = Some((x, y));
                output.info.transform = transform.into();
            }
            wl_output::Event::Mode {
                flags,
                width,
                height,
                ..
            } => {
                if matches!(flags,WEnum::Value(flags) if flags.contains(wl_output::Mode::Current))
                    && width > 0
                    && height > 0
                {
                    output.info.width = width as u32;
                    output.info.height = height as u32;
                }
            }
            wl_output::Event::Description { description } => {
                output.info.description = description.chars().take(255).collect()
            }
            _ => (),
        }
    }
}
impl<T: 'static> Dispatch<session::ExtImageCopyCaptureSessionV1, ()> for State<T> {
    fn event(
        state: &mut Self,
        _: &session::ExtImageCopyCaptureSessionV1,
        event: session::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if state.error.is_some() {
            return;
        }
        match event {
            session::Event::BufferSize { width, height } => {
                state.batch();
                if state.has_size
                    || width == 0
                    || height == 0
                    || width > i32::MAX as u32
                    || height > i32::MAX as u32
                {
                    state.stop(-libc::EPROTO);
                } else {
                    state.has_size = true;
                    state.batch().width = width;
                    state.batch().height = height;
                }
            }
            session::Event::ShmFormat { format } => state.format(Format {
                format: format.into(),
                modifier: 0,
                dmabuf: false,
            }),
            session::Event::DmabufDevice { device } => {
                if device.len() != size_of::<libc::dev_t>() || state.batch().device.is_some() {
                    state.stop(-libc::EPROTO);
                } else {
                    state.batch().device =
                        Some(libc::dev_t::from_ne_bytes(device.try_into().unwrap()));
                }
            }
            session::Event::DmabufFormat { format, modifiers } => {
                state.batch();
                if !modifiers.len().is_multiple_of(8) {
                    state.stop(-libc::EPROTO);
                } else if (state.accept)(format, true) {
                    if modifiers.len() / 8 > 256 {
                        state.stop(-libc::EOVERFLOW);
                    } else {
                        for bytes in modifiers.as_chunks::<8>().0 {
                            state.format(Format {
                                format,
                                modifier: u64::from_ne_bytes(*bytes),
                                dmabuf: true,
                            });
                        }
                    }
                }
            }
            session::Event::Done => {
                if !state.has_size || state.batch.as_ref().is_none_or(|b| b.formats.is_empty()) {
                    state.stop(-libc::EPROTO);
                } else if let Some(generation) = state.current.generation.checked_add(1) {
                    let mut batch = state.batch.take().unwrap();
                    batch.generation = generation;
                    state.current = batch.clone();
                    state.push(Event::Constraints(batch));
                } else {
                    state.stop(-libc::EOVERFLOW);
                }
            }
            session::Event::Stopped => state.stop(-libc::ECANCELED),
            _ => (),
        }
    }
}
impl<T: 'static> Dispatch<frame::ExtImageCopyCaptureFrameV1, ()> for State<T> {
    fn event(
        state: &mut Self,
        _: &frame::ExtImageCopyCaptureFrameV1,
        event: frame::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        let Some(pending) = state.pending.as_mut() else {
            state.stop(-libc::EPROTO);
            return;
        };
        match event {
            frame::Event::Transform { transform } => {
                if pending.transform || !matches!(transform, WEnum::Value(_)) {
                    state.stop(-libc::EPROTO);
                } else {
                    pending.transform = true;
                    pending.frame.transform = match transform {
                        WEnum::Value(value) => value as u32,
                        _ => unreachable!(),
                    };
                }
            }
            frame::Event::PresentationTime {
                tv_sec_hi,
                tv_sec_lo,
                tv_nsec,
            } => {
                let time = ((u64::from(tv_sec_hi) << 32) | u64::from(tv_sec_lo))
                    .checked_mul(1_000_000_000)
                    .and_then(|n| n.checked_add(u64::from(tv_nsec)));
                if pending.time || tv_nsec >= 1_000_000_000 {
                    state.stop(-libc::EPROTO);
                } else if let Some(time) = time {
                    pending.time = true;
                    pending.frame.presentation_ns = time;
                } else {
                    state.stop(-libc::EPROTO);
                }
            }
            frame::Event::Damage {
                x,
                y,
                width,
                height,
            } => {
                if x < 0
                    || y < 0
                    || width < 0
                    || height < 0
                    || u64::from(x as u32) + width as u64 > u64::from(pending.buffer.0.size.0)
                    || u64::from(y as u32) + height as u64 > u64::from(pending.buffer.0.size.1)
                {
                    state.stop(-libc::EPROTO);
                } else if width != 0 && height != 0 {
                    let (left, top, right, bottom) = pending.frame.damage.map_or(
                        (x, y, x + width, y + height),
                        |(a, b, c, d)| {
                            (
                                a.min(x),
                                b.min(y),
                                (a + c).max(x + width),
                                (b + d).max(y + height),
                            )
                        },
                    );
                    pending.frame.damage = Some((left, top, right - left, bottom - top));
                }
            }
            frame::Event::Ready => {
                if !pending.time || !pending.transform {
                    state.stop(-libc::EPROTO);
                }
                state.finish(0);
            }
            frame::Event::Failed { reason } => {
                let status = match reason {
                    WEnum::Value(frame::FailureReason::BufferConstraints) => {
                        state.rejected = pending.frame.generation;
                        -libc::ESTALE
                    }
                    WEnum::Value(frame::FailureReason::Stopped) => {
                        state.stop(-libc::ECANCELED);
                        -libc::ECANCELED
                    }
                    _ => -libc::EAGAIN,
                };
                state.finish(status);
            }
            _ => (),
        }
    }
}
impl<T: 'static> Dispatch<dmabuf::ZwpLinuxDmabufV1, ()> for State<T> {
    fn event(
        _: &mut Self,
        _: &dmabuf::ZwpLinuxDmabufV1,
        _: dmabuf::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}
impl<T: 'static> Dispatch<params::ZwpLinuxBufferParamsV1, ()> for State<T> {
    fn event(
        state: &mut Self,
        _: &params::ZwpLinuxBufferParamsV1,
        _: params::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        state.stop(-libc::EPROTO);
    }
}
delegate_noop!(@<T:'static> State<T>:ignore manager::ExtImageCopyCaptureManagerV1);
delegate_noop!(@<T:'static> State<T>:ignore sources::ExtOutputImageCaptureSourceManagerV1);
delegate_noop!(@<T:'static> State<T>:ignore source::ExtImageCaptureSourceV1);
delegate_noop!(@<T:'static> State<T>:ignore wl_buffer::WlBuffer);

impl<T: 'static> Drop for Capture<T> {
    fn drop(&mut self) {
        self.disconnect();
    }
}

delegate_noop!(@<T:'static> State<T>:ignore wl_shm::WlShm);
delegate_noop!(@<T:'static> State<T>:ignore wl_shm_pool::WlShmPool);
delegate_noop!(@<T:'static> State<T>:ignore output_manager::ZxdgOutputManagerV1);
impl<T: 'static> Dispatch<logical_output::ZxdgOutputV1, u32> for State<T> {
    fn event(
        state: &mut Self,
        _: &logical_output::ZxdgOutputV1,
        event: logical_output::Event,
        global: &u32,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let logical_output::Event::LogicalPosition { x, y } = event
            && let Some(output) = state
                .outputs
                .iter_mut()
                .find(|output| output.global == *global)
        {
            output.logical_position = Some((x, y));
        }
    }
}
