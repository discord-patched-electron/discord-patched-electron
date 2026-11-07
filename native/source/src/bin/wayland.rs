// SPDX-License-Identifier: GPL-3.0-only
//! Typed compositor destination -> completed native PipeWire DMA-BUF.
//! Protocol, native stream and GPU storage owners retire independently.
use dpe_graphics::dmabuf::{self, Allocation, Allocator};
use dpe_pipewire::{
    ffi as f, pod,
    source::{Buffer, Driver, Event as PwEvent, MainLoop},
    source_dmabuf,
    source_pods::{self, Format as PwFormat},
};
use dpe_source::{Command, Signals, Stdin, emit, quoted};
use dpe_wayland::capture::{Buffer as WlBuffer, Capture, Constraints, Event, Format, Plane};
use std::{
    cell::{Cell, RefCell},
    ffi::CString,
    os::fd::AsRawFd,
    rc::Rc,
    time::{Duration, Instant},
};
type Result<T> = std::result::Result<T, &'static str>;
const FORMATS: [u32; 4] = [
    u32::from_le_bytes(*b"AR24"),
    u32::from_le_bytes(*b"XR24"),
    u32::from_le_bytes(*b"AB24"),
    u32::from_le_bytes(*b"XB24"),
];
fn spa(format: u32) -> Option<u32> {
    FORMATS
        .iter()
        .position(|value| *value == format)
        .map(|index| {
            [
                f::spa_video_format_SPA_VIDEO_FORMAT_BGRA,
                f::spa_video_format_SPA_VIDEO_FORMAT_BGRx,
                f::spa_video_format_SPA_VIDEO_FORMAT_RGBA,
                f::spa_video_format_SPA_VIDEO_FORMAT_RGBx,
            ][index]
        })
}
fn drm(format: u32) -> Option<u32> {
    FORMATS
        .into_iter()
        .find(|value| spa(*value) == Some(format))
}
struct Options {
    output: String,
    fps: u32,
}
impl Options {
    fn parse(args: impl Iterator<Item = String>) -> Option<Self> {
        let mut args = args;
        let mut output = None;
        let mut fps = None;
        while let Some(key) = args.next() {
            let value = args.next()?;
            match key.as_str() {
                "--output" if output.is_none() => output = Some(value),
                "--fps" if fps.is_none() => {
                    fps = Some(
                        value
                            .parse::<u32>()
                            .ok()
                            .filter(|value| (1..=240).contains(value))?,
                    )
                }
                _ => return None,
            }
        }
        let output = output?;
        (!output.is_empty() && output.len() < 128).then_some(Self { output, fps: fps? })
    }
}
type Pixels = Rc<Allocation>;
type Compositor = Capture<Pixels>;
struct Storage {
    image: WlBuffer<Pixels>,
}
struct Layout {
    constraints: Constraints,
    node: Option<CString>,
    allocator: Option<Rc<Allocator>>,
    chosen: Option<Format>,
    probe: Option<Pixels>,
    planes: u32,
}
impl Layout {
    fn compatible(&self, format: Format) -> bool {
        self.constraints.formats.contains(&format)
    }
    fn offers(&self, fps: u32) -> Result<Vec<source_pods::Pod>> {
        let size = |format| PwFormat {
            width: self.constraints.width,
            height: self.constraints.height,
            format,
            modifier: 0,
        };
        if let Some(chosen) = self.chosen.filter(|format| self.compatible(*format)) {
            return Ok(vec![
                source_pods::offer(
                    PwFormat {
                        modifier: chosen.modifier,
                        ..size(spa(chosen.format).ok_or("unadvertised-format")?)
                    },
                    fps,
                )
                .ok_or("no-supported-gpu-format")?,
            ]);
        }
        self.choices(fps)
    }
    fn choices(&self, fps: u32) -> Result<Vec<source_pods::Pod>> {
        let size = |format| PwFormat {
            width: self.constraints.width,
            height: self.constraints.height,
            format,
            modifier: 0,
        };
        let mut offers = Vec::with_capacity(4);
        for format in FORMATS {
            let modifiers: Vec<_> = self
                .constraints
                .formats
                .iter()
                .filter(|entry| {
                    entry.dmabuf && entry.format == format && entry.modifier != u64::MAX
                })
                .map(|entry| entry.modifier)
                .collect();
            if !modifiers.is_empty() {
                offers.push(
                    source_pods::offer_modifiers(size(spa(format).unwrap()), fps, &modifiers, true)
                        .ok_or("no-supported-gpu-format")?,
                );
            }
        }
        if offers.is_empty() {
            Err("no-supported-gpu-format")
        } else {
            Ok(offers)
        }
    }
}
fn fixate(
    layout: &RefCell<Layout>,
    pod: &pod::Owned,
    fps: u32,
) -> Result<Option<Vec<source_pods::Pod>>> {
    let view = pod.format();
    let (required, modifiers) = view.modifiers().ok_or("invalid-modifier-choice")?;
    if !required {
        return Ok(None);
    }
    let dimensions = view.dimensions().ok_or("gpu-format-negotiation-failed")?;
    let format = drm(view.pixel_format().ok_or("gpu-format-negotiation-failed")?)
        .ok_or("gpu-format-negotiation-failed")?;
    let mut layout = layout.borrow_mut();
    if dimensions != (layout.constraints.width, layout.constraints.height) {
        return Ok(None);
    }
    let accepted: Vec<_> = modifiers
        .into_iter()
        .filter(|modifier| {
            layout.compatible(Format {
                format,
                modifier: *modifier,
                dmabuf: true,
            })
        })
        .collect();
    if accepted.is_empty() {
        return Err("no-shared-gpu-modifier");
    }
    let allocator = layout
        .allocator
        .as_ref()
        .ok_or("render-device-unavailable")?;
    let storage = allocator
        .create_modifiers(dimensions.0, dimensions.1, format, &accepted)
        .ok_or("no-shared-gpu-modifier")?;
    layout.chosen = Some(Format {
        format,
        modifier: storage.modifier,
        dmabuf: true,
    });
    layout.planes = storage.planes().len() as u32;
    layout.probe = Some(Rc::new(storage));
    let mut offers = layout.offers(fps)?;
    offers.extend(layout.choices(fps)?);
    Ok(Some(offers))
}
// Native's synchronous ALLOC_BUFFERS boundary calls this only while its exact
// SPA records are writable and no native dispatch can retire their pointers.
unsafe fn allocate_native(
    layout: &RefCell<Layout>,
    capture: &RefCell<Compositor>,
    native: *mut f::spa_buffer,
) -> Result<Storage> {
    let (constraints, format, storage) = {
        let mut layout = layout.borrow_mut();
        let format = layout.chosen.ok_or("GPU layout not negotiated")?;
        let allocator = layout.allocator.clone().ok_or("Missing GPU allocator")?;
        let storage = layout
            .probe
            .take()
            .or_else(|| {
                allocator
                    .create(
                        layout.constraints.width,
                        layout.constraints.height,
                        format.format,
                        format.modifier,
                    )
                    .map(Rc::new)
            })
            .ok_or("GPU allocation failed")?;
        (layout.constraints.clone(), format, storage)
    };
    let planes: Vec<_> = storage
        .planes()
        .iter()
        .map(|plane| Plane {
            descriptor: plane.descriptor(),
            stride: plane.stride,
            offset: plane.offset,
        })
        .collect();
    let image = capture
        .borrow()
        .allocate(&constraints, format, &planes, storage.clone())
        .map_err(|_| "Compositor allocation failed")?;
    let planes: Vec<_> = storage
        .planes()
        .iter()
        .map(|plane| source_dmabuf::Plane {
            descriptor: plane.descriptor(),
            stride: plane.stride,
            offset: plane.offset,
        })
        .collect();
    // SAFETY: the caller's native factory contract pins these complete writable
    // records; image retains every descriptor/allocation beyond this call.
    unsafe {
        source_dmabuf::initialize(native, &planes)?;
    }
    if std::env::var_os("DPE_CAPTURE_PROFILE").is_some_and(|value| value == "1") {
        eprintln!(
            "DPE Wayland allocated {}x{} format={:#x} modifier={:#x} planes={} fds={:?}",
            storage.width,
            storage.height,
            storage.format,
            storage.modifier,
            planes.len(),
            planes
                .iter()
                .map(|plane| plane.descriptor.as_raw_fd())
                .collect::<Vec<_>>()
        );
    }
    Ok(Storage { image })
}
struct Actor {
    driver: Driver<Storage>,
    capture: Rc<RefCell<Compositor>>,
    layout: Rc<RefCell<Layout>>,
    loop_: Rc<MainLoop>,
    signals: Signals,
    stdin: Stdin,
    pending: Option<Buffer<Storage>>,
    fps: u32,
    rate_hint: Rc<Cell<u32>>,
    rate: Option<pod::Fraction>,
    streaming: bool,
    announced: bool,
    stopping: bool,
    due: bool,
    next: Option<Instant>,
    retry: Option<Instant>,
    failures: u32,
    rejected: u64,
    frames: u64,
    skipped: u64,
    startup: Instant,
}
impl Actor {
    fn new(options: Options) -> Result<Self> {
        let capture = Rc::new(RefCell::new(
            Compositor::connect(|format, dma| dma && dmabuf::rgba(format))
                .map_err(|_| "wayland-unavailable")?,
        ));
        {
            let mut capture = capture.borrow_mut();
            if !capture.dma_supported()
                || !capture
                    .inventory()
                    .iter()
                    .any(|output| output.name == options.output && output.transform == 0)
            {
                return Err("capture-output-unavailable");
            }
            capture
                .start(&options.output, true)
                .map_err(|_| "capture-start-failed")?;
        }
        let signals = Signals::new().map_err(|_| "capture-loop-failed")?;
        let stdin = Stdin::new().map_err(|_| "capture-control-invalid")?;
        let loop_ = MainLoop::new().map_err(|_| "pipewire-unavailable")?;
        let layout = Rc::new(RefCell::new(Layout {
            constraints: Constraints::default(),
            node: None,
            allocator: None,
            chosen: None,
            probe: None,
            planes: 0,
        }));
        let factory_layout = layout.clone();
        let factory_capture = capture.clone();
        let properties=c"{ media.class = Video/Source node.name = dpe-wayland-direct node.description = \"DPE selected output\" node.virtual = true }";
        // SAFETY: initialization writes only bounded native plane/chunk records,
        // retains all descriptor/GPU owners in Storage, and does not dispatch
        // either event loop or mutate the stream while its factory is active.

        let driver = unsafe {
            Driver::new(
                loop_.clone(),
                c"DPE direct output capture",
                properties,
                16,
                move |native, _| allocate_native(&factory_layout, &factory_capture, native),
            )
        }
        .map_err(|_| "pipewire-unavailable")?;
        let rate_hint = Rc::new(Cell::new(options.fps));
        let fixation_rate = rate_hint.clone();
        let fixation_layout = layout.clone();
        driver.set_fixation(move |pod| fixate(&fixation_layout, pod, fixation_rate.get()));
        Ok(Self {
            driver,
            capture,
            layout,
            loop_,
            signals,
            stdin,
            pending: None,
            fps: options.fps,
            rate_hint,
            rate: None,
            streaming: false,
            announced: false,
            stopping: false,
            due: false,
            next: None,
            retry: None,
            failures: 0,
            rejected: 0,
            frames: 0,
            skipped: 0,
            startup: Instant::now(),
        })
    }
    fn offer(&self) -> Result<()> {
        let offers = self.layout.borrow().offers(self.fps)?;
        self.driver
            .offer_many(&offers)
            .map_err(|_| "pipewire-connect-failed")
    }
    fn constraints(&mut self, value: Constraints) -> Result<()> {
        if value.width > 16384 || value.height > 16384 {
            return Err("gpu-capture-unavailable");
        }
        let node = dmabuf::render_node(value.device.ok_or("gpu-capture-unavailable")?)
            .ok_or("render-device-unavailable")?;
        {
            let mut layout = self.layout.borrow_mut();
            if let Some(previous) = &layout.node {
                if previous != &node {
                    return Err("capture-device-changed");
                }
            } else {
                layout.allocator = Some(Rc::new(
                    Allocator::open(&node).ok_or("render-device-open-failed")?,
                ));
                layout.node = Some(node);
            }
            let compatible = layout.constraints.width == value.width
                && layout.constraints.height == value.height
                && layout
                    .chosen
                    .is_some_and(|format| value.formats.contains(&format));
            layout.constraints = value;
            if !compatible {
                layout.chosen = None;
                layout.probe = None;
                layout.planes = 0;
                self.rate = None;
            }
        }
        self.next = None;
        self.due = false;
        self.offer()
    }
    fn format(&mut self, pod: pod::Owned) -> Result<()> {
        let format = pod.format();
        if !format.is_raw() {
            return Err("gpu-format-negotiation-failed");
        }
        let dimensions = format.dimensions().ok_or("gpu-format-negotiation-failed")?;
        let fourcc = drm(format
            .pixel_format()
            .ok_or("gpu-format-negotiation-failed")?)
        .ok_or("gpu-format-negotiation-failed")?;
        let (fixation, modifiers) = format.modifiers().ok_or("invalid-modifier-choice")?;
        if std::env::var_os("DPE_CAPTURE_PROFILE").is_some_and(|value| value == "1") {
            eprintln!(
                "DPE Wayland format {dimensions:?} {fourcc:#x} fixation={fixation} modifiers={modifiers:?}"
            );
        }
        {
            let mut layout = self.layout.borrow_mut();
            if dimensions != (layout.constraints.width, layout.constraints.height) {
                return Ok(());
            }
            let accepted: Vec<_> = modifiers
                .into_iter()
                .filter(|modifier| {
                    layout.compatible(Format {
                        format: fourcc,
                        modifier: *modifier,
                        dmabuf: true,
                    })
                })
                .collect();
            if accepted.is_empty() {
                return Err("unadvertised-format");
            }
            let compatible = !fixation
                && accepted.len() == 1
                && layout.chosen
                    == Some(Format {
                        format: fourcc,
                        modifier: accepted[0],
                        dmabuf: true,
                    })
                && (1..=4).contains(&layout.planes);
            if !compatible {
                let allocator = layout
                    .allocator
                    .as_ref()
                    .ok_or("render-device-unavailable")?;
                let storage = allocator
                    .create_modifiers(dimensions.0, dimensions.1, fourcc, &accepted)
                    .ok_or("no-shared-gpu-modifier")?;
                layout.chosen = Some(Format {
                    format: fourcc,
                    modifier: storage.modifier,
                    dmabuf: true,
                });
                layout.planes = storage.planes().len() as u32;
                layout.probe = Some(Rc::new(storage));
            }
        }
        if fixation {
            return self.offer();
        }
        let rate = format
            .rate()
            .filter(|rate| rate.numerator > 0)
            .or_else(|| format.maximum())
            .filter(|rate| {
                rate.denominator > 0
                    && rate.numerator >= rate.denominator
                    && u64::from(rate.numerator) <= 240 * u64::from(rate.denominator)
            })
            .ok_or("invalid-negotiated-frame-rate")?;
        self.rate = Some(rate);
        self.next = None;
        self.due = false;
        let planes = self.layout.borrow().planes;
        let pods = source_pods::plain_buffers(planes).ok_or("gpu-allocation-unsupported")?;
        self.driver
            .buffers(&pods)
            .map_err(|_| "pipewire-unavailable")
    }
    fn process(&mut self) -> Result<()> {
        if !std::mem::take(&mut self.due)
            || self.pending.is_some()
            || !self.streaming
            || self.rate.is_none()
        {
            return Ok(());
        }
        let Some(buffer) = self.driver.dequeue().map_err(|_| "pipewire-unavailable")? else {
            self.skipped += 1;
            return Ok(());
        };
        let image = buffer.storage().image.clone();
        let result = {
            let mut capture = self.capture.borrow_mut();
            capture
                .refresh(&image)
                .and_then(|()| capture.submit(image).map_err(|(error, _)| error))
        };
        match result {
            Ok(()) => self.pending = Some(buffer),
            Err(error) => {
                self.driver
                    .return_unused(&buffer)
                    .map_err(|_| "pipewire-unavailable")?;
                if ![-libc::ESTALE, -libc::EAGAIN, -libc::EBUSY].contains(&error) {
                    return Err("capture-request-failed");
                }
            }
        }
        Ok(())
    }
    fn compositor_events(&mut self) -> Result<()> {
        loop {
            let event = self.capture.borrow_mut().next_event();
            let Some(event) = event else {
                break;
            };
            match event {
                Event::Constraints(value) => self.constraints(value)?,
                Event::Stopped(reason) => {
                    return Err(match reason {
                        value if value == -libc::EOVERFLOW => "capture-format-capacity-exceeded",
                        value if value == -libc::EPROTO => "capture-protocol-invalid",
                        value if value == -libc::ENODEV => "capture-output-removed",
                        _ => "capture-source-stopped",
                    });
                }
                Event::Frame {
                    buffer: image,
                    frame,
                    status,
                } => {
                    let buffer = self.pending.take().ok_or("capture-ownership-mismatch")?;
                    if !buffer.storage().image.same_image(&image) {
                        return Err("capture-ownership-mismatch");
                    }
                    if !buffer.live() {
                        continue;
                    }
                    if status < 0
                        || frame.generation != self.layout.borrow().constraints.generation
                        || !self.streaming
                    {
                        self.driver
                            .return_unused(&buffer)
                            .map_err(|_| "pipewire-unavailable")?;
                        self.skipped += 1;
                        if status == -libc::EAGAIN {
                            self.failures = (self.failures + 1).min(5);
                            self.retry = Some(
                                Instant::now()
                                    + Duration::from_millis(
                                        (20 << self.failures.saturating_sub(1)).min(250),
                                    ),
                            );
                        } else if status == -libc::ESTALE {
                            self.rejected = frame.generation;
                        } else if status < 0 {
                            return Err("capture-frame-failed");
                        }
                        continue;
                    }
                    self.failures = 0;
                    self.retry = None;
                    if frame.transform != 0 {
                        return Err("transform-unsupported");
                    }
                    buffer
                        .set_header(frame.presentation_ns, self.frames)
                        .map_err(|_| "capture-protocol-invalid")?;
                    self.driver
                        .queue(&buffer)
                        .map_err(|_| "pipewire-queue-failed")?;
                    self.frames += 1;
                }
            }
        }
        Ok(())
    }
    fn stream_events(&mut self) -> Result<()> {
        while let Some(event) = self.driver.next_event() {
            match event {
                PwEvent::State { next, error, .. } => {
                    self.streaming = next == f::pw_stream_state_PW_STREAM_STATE_STREAMING;
                    self.next = None;
                    self.due = false;
                    if next == f::pw_stream_state_PW_STREAM_STATE_ERROR {
                        eprintln!(
                            "DPE Wayland PipeWire: {}; source: {:?}",
                            error.as_deref().unwrap_or("stream failed"),
                            self.driver.error()
                        );
                        return Err("pipewire-unavailable");
                    }
                    if next == f::pw_stream_state_PW_STREAM_STATE_PAUSED
                        && !self.announced
                        && let Some(node) = self.driver.node_id()
                    {
                        self.announced = true;
                        let layout = self.layout.borrow();
                        emit(format!(
                            "{{\"event\":\"ready\",\"backend\":\"wayland-direct\",\"nodeId\":{node},\"width\":{},\"height\":{},\"generation\":{}}}",
                            layout.constraints.width,
                            layout.constraints.height,
                            layout.constraints.generation
                        ));
                        // SAFETY: initialization has reached a usable node; steady
                        // cancellation belongs to stdin/signalfd and the parent.
                        unsafe {
                            libc::alarm(0);
                        }
                    }
                }
                PwEvent::Format(Some(pod)) => self.format(pod)?,
                PwEvent::Format(None) => self.rate = None,
                PwEvent::Process => self.process()?,
                PwEvent::Failed(reason) => {
                    eprintln!("DPE Wayland source: {reason}");
                    return Err("pipewire-unavailable");
                }
                PwEvent::Added(_) | PwEvent::Removed(_) => (),
            }
        }
        Ok(())
    }
    fn run(&mut self) -> Result<()> {
        while !self.stopping {
            self.loop_.iterate(0).map_err(|_| "pipewire-unavailable")?;
            self.capture
                .borrow_mut()
                .dispatch()
                .map_err(|_| "wayland-disconnected")?;
            self.compositor_events()?;
            self.stream_events()?;
            let mut timeout = None;
            if !self.announced {
                let remaining = Duration::from_secs(8).saturating_sub(self.startup.elapsed());
                if remaining.is_zero() {
                    return Err("capture-start-timeout");
                }
                timeout = Some(remaining);
            }
            if self.streaming
                && self.pending.is_none()
                && self.layout.borrow().constraints.generation > self.rejected
                && let Some(rate) = self.rate
            {
                let period =
                    Duration::from_nanos((1_000_000_000 / u64::from(self.fps)).max(
                        1_000_000_000 * u64::from(rate.denominator) / u64::from(rate.numerator),
                    ));
                let now = Instant::now();
                let next = self.next.get_or_insert(now);
                let due = (*next).max(self.retry.unwrap_or(*next));
                if now >= due {
                    let remainder = now.duration_since(*next).as_nanos() % period.as_nanos();
                    *next = now + period - Duration::from_nanos(remainder as u64);
                    if !self.due {
                        self.due = true;
                        self.driver.trigger().map_err(|_| "pipewire-unavailable")?;
                    }
                }
                let delay = (*next)
                    .max(self.retry.unwrap_or(*next))
                    .saturating_duration_since(Instant::now());
                timeout = Some(timeout.map_or(delay, |limit| limit.min(delay)));
            }
            if self.driver.has_events() {
                timeout = Some(Duration::ZERO);
            }
            let mut polls = [
                libc::pollfd {
                    fd: self.loop_.fd(),
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: self.capture.borrow().descriptor().as_raw_fd(),
                    events: libc::POLLIN
                        | if self.capture.borrow().needs_flush() {
                            libc::POLLOUT
                        } else {
                            0
                        },
                    revents: 0,
                },
                libc::pollfd {
                    fd: 0,
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: self.signals.descriptor(),
                    events: libc::POLLIN,
                    revents: 0,
                },
            ];
            let time = timeout.map(|delay| libc::timespec {
                tv_sec: delay.as_secs().min(i64::MAX as u64) as _,
                tv_nsec: delay.subsec_nanos() as _,
            });
            let time = time
                .as_ref()
                .map_or(std::ptr::null(), |time| time as *const _);
            // SAFETY: bounded initialized descriptors and optional local timeout;
            // connections/streams remain retained across this non-dispatch wait.
            let status = unsafe {
                libc::ppoll(polls.as_mut_ptr(), polls.len() as _, time, std::ptr::null())
            };
            if status < 0 {
                if std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
                    continue;
                }
                return Err("capture-loop-failed");
            }
            if polls[3].revents != 0 {
                return Ok(());
            }
            if polls[2].revents != 0 {
                let previous = self.fps;
                let stopping = &mut self.stopping;
                let fps = &mut self.fps;
                if !self
                    .stdin
                    .read(|command| {
                        if !*stopping {
                            match command {
                                Command::Stop => *stopping = true,
                                Command::Rate(value) => *fps = value,
                            }
                        }
                    })
                    .map_err(|_| "capture-control-invalid")?
                    || self.stopping
                {
                    return Ok(());
                }
                if self.fps != previous {
                    self.rate_hint.set(self.fps);
                    self.next = None;
                    self.due = false;
                    self.offer()?;
                }
            }
        }
        Ok(())
    }
}
impl Drop for Actor {
    fn drop(&mut self) {
        self.capture.borrow_mut().disconnect();
        drop(self.pending.take());
    }
}
fn list() {
    match Compositor::connect(|format, dma| dma && dmabuf::rgba(format)) {
        Ok(capture) => {
            let available = capture.dma_supported();
            let outputs:Vec<_>=capture.inventory().into_iter().map(|output|{let position=output.position.map_or(String::new(),|(x,y)|format!(",\"position\":{{\"x\":{x},\"y\":{y}}}"));let supported=available&&output.transform==0;format!("{{\"id\":{},\"name\":{},\"description\":{}{position},\"width\":{},\"height\":{},\"transform\":{},\"supported\":{supported},\"reason\":{}}}",quoted(&output.name),quoted(&output.name),quoted(&output.description),output.width,output.height,output.transform,if !available{"\"protocol-unavailable\""}else if !supported{"\"transform-unsupported\""}else{"null"})}).collect();
            emit(format!(
                "{{\"version\":1,\"backend\":\"wayland-direct\",\"available\":{available},\"reason\":{},\"outputs\":[{}]}}",
                if available {
                    "null"
                } else {
                    "\"protocol-unavailable\""
                },
                outputs.join(",")
            ));
        }
        Err(_) => emit(
            "{\"version\":1,\"backend\":\"wayland-direct\",\"available\":false,\"reason\":\"wayland-unavailable\",\"outputs\":[]}",
        ),
    }
}
fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    // SAFETY: dedicated process watchdog and parent death signal. Check the
    // parent identity around installation; no other process is reconfigured.
    let okay = unsafe {
        let parent = libc::getppid();
        libc::alarm(8);
        libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) == 0 && libc::getppid() == parent
    };
    if !okay {
        std::process::exit(3);
    }
    if args.as_slice() == ["--list"] {
        list();
        return;
    }
    let Some(options) = Options::parse(args.into_iter()) else {
        std::process::exit(2);
    };
    let mut actor = match Actor::new(options) {
        Ok(actor) => actor,
        Err(reason) => {
            emit(format!(
                "{{\"event\":\"error\",\"backend\":\"wayland-direct\",\"reason\":{}}}",
                quoted(reason)
            ));
            std::process::exit(3);
        }
    };
    let result = actor.run();
    if let Err(reason) = &result {
        emit(format!(
            "{{\"event\":\"error\",\"backend\":\"wayland-direct\",\"reason\":{}}}",
            quoted(reason)
        ));
    }
    let (frames, skipped) = (actor.frames, actor.skipped);
    drop(actor);
    emit(format!(
        "{{\"event\":\"ended\",\"backend\":\"wayland-direct\",\"failed\":{},\"frames\":{frames},\"skipped\":{skipped}}}",
        result.is_err()
    ));
    if result.is_err() {
        std::process::exit(1);
    }
}
