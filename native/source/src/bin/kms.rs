// SPDX-License-Identifier: GPL-3.0-only
//! Explicit KMS selection -> owned GPU snapshots -> PipeWire fallback.
//! Optimized descriptor capture remains the separate PipeWire-free producer.
use dpe_graphics::kms_capture::{Renderer, Surface};
use dpe_kms::capture::{self, Capture, Pending, Response};
use dpe_pipewire::{
    ffi as f,
    publish::{Image, Plane, Publisher},
    source::{Buffer, Driver, Event, MainLoop},
    source_pods::{self, Format},
};
use dpe_source::{Command, Signals, Stdin, emit, quoted};
use std::{
    collections::{HashMap, HashSet},
    ffi::CString,
    os::fd::AsRawFd,
    rc::Rc,
    time::{Duration, Instant},
};
type Result<T> = std::result::Result<T, &'static str>;
struct Config {
    selected: CString,
    render: CString,
    fps: u32,
}
impl Config {
    fn parse(arguments: impl Iterator<Item = String>) -> Option<Self> {
        let mut arguments = arguments;
        let mut selected = None;
        let mut render = CString::new("auto").ok()?;
        let mut fps = None;
        let mut seen = HashSet::new();
        while let Some(key) = arguments.next() {
            if !seen.insert(key.clone()) {
                return None;
            }
            let value = arguments.next()?;
            match key.as_str() {
                "--output" => selected = Some(CString::new(value).ok()?),
                "--render-node" => render = CString::new(value).ok()?,
                "--authorize" if value == "true" => (), // Saved shared permission still applies.
                "--fps" if value.bytes().all(|byte| byte.is_ascii_digit()) => {
                    fps = value
                        .parse::<u32>()
                        .ok()
                        .filter(|fps| (1..=240).contains(fps))
                }
                _ => return None,
            }
        }
        Some(Self {
            selected: selected?,
            render,
            fps: fps?,
        })
    }
}
fn list() {
    let outputs = capture::outputs();
    let status = capture::helper_status().and_then(|status| status.to_str().ok());
    let reason = status.or_else(|| {
        (!outputs.iter().any(|output| output.supported)).then_some(if outputs.is_empty() {
            "no-outputs"
        } else {
            "unsupported"
        })
    });
    let entries: Vec<_> = outputs.iter().map(|output| format!("{{\"id\":{},\"name\":{},\"description\":{},\"width\":{},\"height\":{},\"supported\":{},\"reason\":{}}}", quoted(&output.id), quoted(&output.name), quoted(&output.name), output.width, output.height, reason.is_none() && output.supported, if output.supported { "null" } else { "\"unsupported\"" })).collect();
    emit(format!(
        "{{\"version\":1,\"backend\":\"kms\",\"available\":{},\"reason\":{},\"authorizationRequired\":{},\"outputs\":[{}]}}",
        reason.is_none(),
        reason.map_or("null".to_owned(), quoted),
        capture::authorization_required(),
        entries.join(",")
    ));
}
fn capture_error(error: capture::Error) -> &'static str {
    match error {
        capture::Error::Session(reason) => reason.to_str().unwrap_or("capture-helper-failed"),
        capture::Error::Permission => "permission-required",
        capture::Error::Cancelled => "capture-cancelled",
        capture::Error::Unavailable | capture::Error::Unsupported => "unsupported",
        capture::Error::Transport => "capture-helper-failed",
    }
}
fn timestamp() -> Result<u64> {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: one initialized monotonic-clock output, with no retained pointers.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } != 0 {
        return Err("capture-clock-failed");
    }
    let seconds = u64::try_from(time.tv_sec).map_err(|_| "capture-clock-failed")?;
    let nanos = u64::try_from(time.tv_nsec).map_err(|_| "capture-clock-failed")?;
    if nanos >= 1_000_000_000 {
        return Err("capture-clock-failed");
    }
    seconds
        .checked_mul(1_000_000_000)
        .and_then(|value| value.checked_add(nanos))
        .ok_or("capture-clock-failed")
}
struct Storage {
    surface: Surface,
    publisher: Publisher,
}
struct Read {
    request: Pending,
    buffer: Buffer<Storage>,
}
struct Actor {
    // Stream teardown precedes pool/context destruction. Publishers retain the
    // loop until their native registrations have all been removed.
    driver: Driver<Storage>,
    pool: HashMap<u64, Buffer<Storage>>,
    retired: Vec<Buffer<Storage>>,
    _renderer: Rc<Renderer>,
    loop_: Rc<MainLoop>,
    capture: Option<Capture>,
    pending: Option<Read>,
    signals: Signals,
    stdin: Stdin,
    dimensions: (u32, u32),
    fps: u32,
    negotiated_fps: u32,
    streaming: bool,
    negotiated: bool,
    announced: bool,
    stopping: bool,
    due: bool,
    next: Option<Instant>,
    changed: Option<Instant>,
    frames: u64,
    releases: u64,
    skipped: u64,
}
impl Actor {
    fn new(config: Config) -> Result<Self> {
        if let Some(status) = capture::helper_status() {
            return Err(status.to_str().unwrap_or("capture-helper-failed"));
        }
        let capture = Capture::open(&config.selected, &config.render).map_err(capture_error)?;
        let dimensions = capture.dimensions();
        let signals = Signals::new().map_err(|_| "capture-failed")?;
        let stdin = Stdin::new().map_err(|_| "capture-control-invalid")?;
        let loop_ = MainLoop::new().map_err(|_| "pipewire-unavailable")?;
        let renderer = Rc::new(Renderer::open(capture.render_path()).ok_or("capture-gpu-failed")?);
        if !Publisher::supported(renderer.device()) {
            return Err("capture-sync-unavailable");
        }
        let allocation = renderer.clone();
        let owner = loop_.clone();
        // SAFETY: the factory initializes only the supplied SPA plane/chunk
        // storage, synchronously. It retains exports, timelines and the loop in
        // Storage and never dispatches or mutates the native stream.
        let driver = unsafe { Driver::new(loop_.clone(), c"DPE KMS monitor capture",
            c"{ media.class = Video/Source node.name = dpe-kms-capture node.description = \"DPE selected monitor\" node.virtual = true }", 16,
            move |buffer, generation| {
                let surface = allocation.allocate(dimensions.0, dimensions.1).ok_or("capture-gpu-failed")?;
                let export = surface.export();
                let planes = [Plane { descriptor: export.descriptor, stride: export.stride, offset: 0 }];
                let image = Image { generation, width: export.width, height: export.height, format: export.format, modifier: export.modifier, planes: &planes };
                let publisher = Publisher::new(owner.clone(), allocation.device(), buffer, image).ok_or("capture-sync-unavailable")?;
                Ok(Storage { surface, publisher })
            }) }.map_err(|_| "pipewire-unavailable")?;
        let result = Self {
            driver,
            pool: HashMap::with_capacity(16),
            retired: Vec::with_capacity(16),
            _renderer: renderer,
            loop_,
            capture: Some(capture),
            pending: None,
            signals,
            stdin,
            dimensions,
            fps: config.fps,
            negotiated_fps: 0,
            streaming: false,
            negotiated: false,
            announced: false,
            stopping: false,
            due: false,
            next: None,
            changed: None,
            frames: 0,
            releases: 0,
            skipped: 0,
        };
        result.offer()?;
        Ok(result)
    }
    fn offer(&self) -> Result<()> {
        let pod = source_pods::offer(
            Format {
                width: self.dimensions.0,
                height: self.dimensions.1,
                format: f::spa_video_format_SPA_VIDEO_FORMAT_BGRA,
                modifier: 0,
            },
            self.fps,
        )
        .ok_or("unsupported")?;
        self.driver.offer(&pod).map_err(|_| "pipewire-unavailable")
    }
    fn reap(&mut self) -> Result<()> {
        for buffer in self.pool.values().chain(&self.retired) {
            if let Some(success) = buffer.storage().publisher.take_completion() {
                if !success {
                    return Err("capture-sync-failed");
                }
                self.releases += 1;
            }
        }
        self.retired
            .retain(|buffer| buffer.storage().publisher.busy());
        Ok(())
    }
    fn process(&mut self) -> Result<()> {
        if !std::mem::take(&mut self.due)
            || !self.streaming
            || !self.negotiated
            || self.pending.is_some()
        {
            return Ok(());
        }
        let mut held: [Option<Buffer<Storage>>; 8] = std::array::from_fn(|_| None);
        let mut selected = None;
        for slot in &mut held {
            let Some(buffer) = self.driver.dequeue().map_err(|_| "pipewire-unavailable")? else {
                break;
            };
            if !buffer.storage().publisher.busy() {
                selected = Some(buffer);
                break;
            }
            *slot = Some(buffer);
        }
        for buffer in held.into_iter().flatten() {
            self.driver
                .return_unused(&buffer)
                .map_err(|_| "pipewire-unavailable")?;
        }
        let Some(buffer) = selected else {
            self.skipped += 1;
            return Ok(());
        };
        let capture = self.capture.take().ok_or("capture-helper-failed")?;
        let request = capture.request().map_err(capture_error)?;
        self.pending = Some(Read { request, buffer });
        Ok(())
    }
    fn events(&mut self) -> Result<()> {
        while let Some(event) = self.driver.next_event() {
            match event {
                Event::State { next, error, .. } => {
                    self.streaming = next == f::pw_stream_state_PW_STREAM_STATE_STREAMING;
                    self.due = false;
                    self.next = None;
                    if next == f::pw_stream_state_PW_STREAM_STATE_ERROR {
                        eprintln!(
                            "DPE KMS PipeWire: {}",
                            error.as_deref().unwrap_or("stream failed")
                        );
                        return Err("pipewire-unavailable");
                    }
                    if next == f::pw_stream_state_PW_STREAM_STATE_PAUSED
                        && !self.announced
                        && let Some(node) = self.driver.node_id()
                    {
                        self.announced = true;
                        emit(format!(
                            "{{\"event\":\"ready\",\"backend\":\"kms\",\"nodeId\":{node},\"width\":{},\"height\":{},\"fps\":{},\"handoff\":\"copy\"}}",
                            self.dimensions.0, self.dimensions.1, self.fps
                        ));
                    }
                }
                Event::Format(None) => {
                    self.negotiated = false;
                    self.due = false;
                    self.next = None;
                }
                Event::Format(Some(pod)) => {
                    let format = pod.format();
                    if format.dimensions() != Some(self.dimensions)
                        || format.pixel_format() != Some(f::spa_video_format_SPA_VIDEO_FORMAT_BGRA)
                        || format.modifier() != Some(0)
                    {
                        return Err("unsupported");
                    }
                    let rate = format
                        .rate()
                        .filter(|rate| rate.numerator != 0)
                        .or_else(|| format.maximum())
                        .ok_or("unsupported")?;
                    if rate.denominator == 0
                        || rate.numerator < rate.denominator
                        || u64::from(rate.numerator) > 240 * u64::from(rate.denominator)
                    {
                        return Err("unsupported");
                    }
                    self.negotiated_fps = rate.numerator / rate.denominator;
                    self.negotiated = true;
                    self.next = None;
                    let pods = source_pods::buffers(self.dimensions.0, self.dimensions.1, Some(1))
                        .ok_or("unsupported")?;
                    self.driver
                        .buffers(&pods)
                        .map_err(|_| "pipewire-unavailable")?;
                }
                Event::Added(buffer) => {
                    self.pool.insert(buffer.serial(), buffer);
                }
                Event::Removed(serial) => {
                    if let Some(buffer) = self.pool.remove(&serial)
                        && buffer.storage().publisher.busy()
                    {
                        self.retired.push(buffer);
                    }
                }
                Event::Process => self.process()?,
                Event::Failed(error) => {
                    eprintln!("DPE KMS: {error}");
                    return Err("pipewire-unavailable");
                }
            }
        }
        Ok(())
    }
    fn reply(&mut self) -> Result<()> {
        let Some(Read { request, buffer }) = self.pending.take() else {
            return Ok(());
        };
        match request.receive().map_err(capture_error)? {
            Response::Waiting(request) => self.pending = Some(Read { request, buffer }),
            Response::Ready { capture, frame } => {
                self.capture = Some(capture);
                if !buffer.live() {
                    return Ok(());
                }
                if !self.streaming || !self.negotiated {
                    self.driver
                        .return_unused(&buffer)
                        .map_err(|_| "pipewire-unavailable")?;
                    return Ok(());
                }
                let Some(frame) = frame else {
                    self.skipped += 1;
                    self.driver
                        .return_unused(&buffer)
                        .map_err(|_| "pipewire-unavailable")?;
                    if self.changed.get_or_insert_with(Instant::now).elapsed()
                        >= Duration::from_secs(2)
                    {
                        return Err("capture-layout-changed");
                    }
                    return Ok(());
                };
                self.changed = None;
                let acquire = buffer
                    .storage()
                    .surface
                    .render(&frame)
                    .ok_or("capture-gpu-failed")?;
                // SAFETY: the exact checked-out native buffer is still live;
                // rendering performs no PipeWire dispatch. Submission touches
                // metadata only now; its completion uses owned DRM state only.
                unsafe {
                    buffer.storage().publisher.submit(acquire)?;
                }
                buffer
                    .set_header(timestamp()?, self.frames)
                    .map_err(|_| "unsupported")?;
                self.driver
                    .queue(&buffer)
                    .map_err(|_| "pipewire-unavailable")?;
                self.frames += 1;
            }
        }
        Ok(())
    }
    fn run(&mut self) -> Result<()> {
        while !self.stopping {
            self.loop_.iterate(0).map_err(|_| "pipewire-unavailable")?;
            self.reap()?;
            self.events()?;
            let mut deadline = None;
            if let Some(read) = &self.pending {
                deadline = Some(read.request.remaining());
            } else if self.streaming && self.negotiated {
                let now = Instant::now();
                let next = self.next.get_or_insert(now);
                let rate = self.fps.min(self.negotiated_fps.max(1));
                let period = Duration::from_nanos(1_000_000_000 / u64::from(rate));
                if now >= *next {
                    let remainder = now.duration_since(*next).as_nanos() % period.as_nanos();
                    *next = now + period - Duration::from_nanos(remainder as u64);
                    if !self.due {
                        self.due = true;
                        self.driver.trigger().map_err(|_| "pipewire-unavailable")?;
                    }
                }
                deadline = Some(next.saturating_duration_since(Instant::now()));
            }
            let pending = self
                .pending
                .as_ref()
                .map(|read| read.request.descriptor().as_raw_fd())
                .unwrap_or(-1);
            let mut polls = [
                libc::pollfd {
                    fd: self.loop_.fd(),
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: libc::STDIN_FILENO,
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: self.signals.descriptor(),
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: pending,
                    events: libc::POLLIN,
                    revents: 0,
                },
            ];
            // Native trigger can enqueue an event synchronously; do not sleep
            // on an event already available to the actor.
            if self.driver.has_events() {
                deadline = Some(Duration::ZERO);
            }
            let timeout = deadline.map(|duration| libc::timespec {
                tv_sec: duration.as_secs().min(i64::MAX as u64) as _,
                tv_nsec: duration.subsec_nanos() as _,
            });
            let timeout = timeout
                .as_ref()
                .map_or(std::ptr::null(), |value| value as *const _);
            // SAFETY: this initialized local array and optional timespec remain
            // borrowed through ppoll; signal handling uses the separate signalfd.
            let status = unsafe {
                libc::ppoll(
                    polls.as_mut_ptr(),
                    polls.len() as _,
                    timeout,
                    std::ptr::null(),
                )
            };
            if status < 0 {
                if std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
                    continue;
                }
                return Err("capture-failed");
            }
            if polls[2].revents != 0 {
                return Ok(());
            }
            if polls[1].revents != 0 {
                let previous = self.fps;
                let fps = &mut self.fps;
                let stopping = &mut self.stopping;
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
                    self.due = false;
                    self.next = None;
                    self.offer()?;
                }
            }
            if polls[3].revents != 0 {
                if polls[3].revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
                    return Err("capture-helper-failed");
                }
                self.reply()?;
            }
            if self
                .pending
                .as_ref()
                .is_some_and(|read| read.request.remaining().is_zero())
            {
                return Err("capture-helper-timeout");
            }
        }
        Ok(())
    }
}
impl Drop for Actor {
    fn drop(&mut self) {
        drop(self.pending.take());
        drop(self.capture.take());
    }
}
fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.as_slice() == ["--list"] {
        list();
        return;
    }
    let Some(config) = Config::parse(args.into_iter()) else {
        std::process::exit(2);
    };
    let mut actor = match Actor::new(config) {
        Ok(actor) => actor,
        Err(error) => {
            emit(format!(
                "{{\"event\":\"error\",\"backend\":\"kms\",\"reason\":{},\"errorCode\":{}}}",
                quoted(error),
                quoted(error)
            ));
            std::process::exit(1);
        }
    };
    let result = actor.run();
    if let Err(error) = &result {
        eprintln!("DPE KMS: {error}");
        emit(format!(
            "{{\"event\":\"error\",\"backend\":\"kms\",\"reason\":{},\"errorCode\":{}}}",
            quoted(error),
            quoted(error)
        ));
    }
    let (frames, releases, skipped) = (actor.frames, actor.releases, actor.skipped);
    drop(actor);
    emit(format!(
        "{{\"event\":\"ended\",\"backend\":\"kms\",\"frames\":{frames},\"releases\":{releases},\"skipped\":{skipped},\"failed\":{},\"errorCode\":{}}}",
        result.is_err(),
        quoted(result.err().unwrap_or(""))
    ));
    if result.is_err() {
        std::process::exit(1);
    }
}
