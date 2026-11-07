// SPDX-License-Identifier: GPL-3.0-only
//! Owned game leases -> GPU copy or explicit-sync PipeWire relay.
use dpe_game::protocol::{self as hook, Packet};
use dpe_graphics::game_capture::{self, Gpu, Surface, Texture as Imported};
use dpe_os::wire;
use dpe_pipewire::{
    ffi as f,
    publish::{Image, Plane, Publisher},
    source::{Buffer, Driver, Event, MainLoop},
    source_dmabuf,
    source_pods::{self, Format},
};
use dpe_source::{
    Command, Signals, Stdin, emit,
    hook_input::{Flavor, Peer, Received},
    quoted,
};
use std::{
    cell::RefCell,
    collections::HashSet,
    ffi::CString,
    os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
    path::PathBuf,
    rc::Rc,
    time::{Duration, Instant},
};
type Result<T> = std::result::Result<T, &'static str>;
#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Copy,
    Relay,
}
struct Options {
    fd: i32,
    node: String,
    fps: u32,
    flavor: Flavor,
    pause: bool,
    uuid: Option<[u8; 16]>,
    mode: Mode,
    auto: bool,
}
impl Options {
    fn parse(args: impl Iterator<Item = String>) -> Option<Self> {
        let handoff = std::env::var("DPE_GAME_CAPTURE_HANDOFF").unwrap_or_else(|_| "copy".into());
        if !matches!(handoff.as_str(), "auto" | "copy" | "relay") {
            return None;
        }
        let mut value = Self {
            fd: -1,
            node: "".into(),
            fps: 0,
            flavor: Flavor::Dpe,
            pause: false,
            uuid: None,
            mode: if handoff == "copy" {
                Mode::Copy
            } else {
                Mode::Relay
            },
            auto: handoff == "auto",
        };
        let mut args = args;
        let mut seen = HashSet::new();
        while let Some(key) = args.next() {
            if !seen.insert(key.clone()) {
                return None;
            }
            let argument = args.next()?;
            match key.as_str() {
                "--hook-fd" => value.fd = argument.parse::<u32>().ok()?.try_into().ok()?,
                "--render-node" => value.node = argument,
                "--fps" => value.fps = argument.parse().ok()?,
                "--hook-protocol" if argument == "obs" => value.flavor = Flavor::Obs,
                "--hook-pause" if argument == "yes" => value.pause = true,
                "--device-uuid" if argument.len() == 32 && argument.is_ascii() => {
                    let mut uuid = [0; 16];
                    for (index, byte) in uuid.iter_mut().enumerate() {
                        *byte = u8::from_str_radix(&argument[index * 2..index * 2 + 2], 16).ok()?;
                    }
                    value.uuid = Some(uuid);
                }
                _ => return None,
            }
        }
        if value.flavor == Flavor::Obs {
            value.mode = Mode::Copy;
            value.auto = false;
            value.pause = false;
        }
        (value.fd >= 0
            && (1..=240).contains(&value.fps)
            && (value.node == "auto" || value.node.starts_with("/dev/dri/renderD"))
            && (value.flavor == Flavor::Obs || value.uuid.is_some()))
        .then_some(value)
    }
    fn gpu(&self) -> Option<Rc<Gpu>> {
        let nodes = if self.node == "auto" {
            let mut nodes = std::fs::read_dir("/dev/dri")
                .ok()?
                .filter_map(std::result::Result::ok)
                .map(|entry| entry.path())
                .filter(|path| {
                    path.file_name()
                        .is_some_and(|name| name.as_encoded_bytes().starts_with(b"renderD"))
                })
                .take(16)
                .collect::<Vec<_>>();
            nodes.sort();
            nodes
        } else {
            vec![PathBuf::from(&self.node)]
        };
        for node in nodes {
            let node = CString::new(node.as_os_str().as_encoded_bytes()).ok()?;
            if let Some(gpu) = Gpu::open(&node)
                && (self.flavor == Flavor::Obs || Some(gpu.uuid()) == self.uuid)
            {
                return Some(gpu);
            }
        }
        None
    }
}
struct Texture {
    packet: Packet,
    descriptors: wire::Message,
    imported: Option<Imported>,
}
impl Texture {
    fn fds(&self) -> [BorrowedFd<'_>; 4] {
        let first = self.descriptors.descriptors[0]
            .as_ref()
            .expect("validated first texture descriptor")
            .as_fd();
        let mut fds = [first; 4];
        for (target, fd) in fds
            .iter_mut()
            .zip(&self.descriptors.descriptors[..self.descriptors.count])
        {
            *target = fd
                .as_ref()
                .expect("validated packed texture descriptors")
                .as_fd();
        }
        fds
    }
}
struct Layout {
    texture: Option<Rc<Texture>>,
    mode: Mode,
    gpu: Rc<Gpu>,
    loop_: Rc<MainLoop>,
    size: Option<(u32, u32)>,
}
enum Storage {
    Copy(Surface),
    Relay {
        publisher: Publisher,
        generation: u64,
    },
}
struct Frame {
    packet: Packet,
    texture: Rc<Texture>,
}
enum Held {
    Acquire {
        frame: Frame,
        fence: OwnedFd,
    },
    Ready {
        frame: Frame,
        acquire: Option<OwnedFd>,
    },
    Copy {
        frame: Frame,
        buffer: Buffer<Storage>,
        fence: OwnedFd,
    },
    Relay {
        frame: Frame,
        buffer: Buffer<Storage>,
        retry: Instant,
    },
}
impl Held {
    fn take_ready(slot: &mut Option<Self>) -> Option<(Frame, Option<OwnedFd>)> {
        match slot.take() {
            Some(Self::Ready { frame, acquire }) => Some((frame, acquire)),
            other => {
                *slot = other;
                None
            }
        }
    }
    fn frame(&self) -> &Frame {
        match self {
            Self::Acquire { frame, .. }
            | Self::Ready { frame, .. }
            | Self::Copy { frame, .. }
            | Self::Relay { frame, .. } => frame,
        }
    }
    fn fence(&self) -> i32 {
        match self {
            Self::Acquire { fence, .. } | Self::Copy { fence, .. } => fence.as_raw_fd(),
            _ => -1,
        }
    }
}
fn spa(format: u32) -> Option<u32> {
    match format {
        value if value == u32::from_le_bytes(*b"AR24") => {
            Some(f::spa_video_format_SPA_VIDEO_FORMAT_BGRA)
        }
        value if value == u32::from_le_bytes(*b"XR24") => {
            Some(f::spa_video_format_SPA_VIDEO_FORMAT_BGRx)
        }
        value if value == u32::from_le_bytes(*b"AB24") => {
            Some(f::spa_video_format_SPA_VIDEO_FORMAT_RGBA)
        }
        value if value == u32::from_le_bytes(*b"XB24") => {
            Some(f::spa_video_format_SPA_VIDEO_FORMAT_RGBx)
        }
        _ => None,
    }
}
unsafe fn allocate(layout: &RefCell<Layout>, native: *mut f::spa_buffer) -> Result<Storage> {
    let layout = layout.borrow();
    let texture = layout.texture.as_ref().ok_or("Missing game texture")?;
    let size = layout.size.ok_or("Game format not negotiated")?;
    if layout.mode == Mode::Copy {
        let surface = layout
            .gpu
            .allocate(size.0, size.1)
            .ok_or("GPU output allocation failed")?;
        let export = surface.export();
        let bytes = export
            .stride
            .checked_mul(export.height)
            .ok_or("GPU output extent overflow")?;
        // SAFETY: this synchronous factory owns the complete native records;
        // Surface retains the exported storage/descriptor beyond return.
        unsafe {
            source_dmabuf::initialize_copy(
                native,
                source_dmabuf::Plane {
                    descriptor: export.descriptor,
                    stride: export.stride,
                    offset: 0,
                },
                bytes,
            )?;
        }
        Ok(Storage::Copy(surface))
    } else {
        let fds = texture.fds();
        let planes: Vec<_> = (0..texture.packet.nfd as usize)
            .map(|index| Plane {
                descriptor: fds[index],
                stride: texture.packet.strides[index],
                offset: texture.packet.offsets[index],
            })
            .collect();
        let image = Image {
            generation: texture.packet.generation,
            width: texture.packet.width,
            height: texture.packet.height,
            format: texture.packet.format,
            modifier: texture.packet.modifier,
            planes: &planes,
        };
        // SAFETY: the factory pins native allocation metadata; the publisher
        // duplicates every retained input descriptor and owns its loop watches.
        let publisher =
            unsafe { Publisher::new(layout.loop_.clone(), layout.gpu.device(), native, image) }
                .ok_or("Relay buffer synchronization unavailable")?;
        Ok(Storage::Relay {
            publisher,
            generation: texture.packet.generation,
        })
    }
}
struct Actor {
    driver: Driver<Storage>,
    peer: Peer,
    layout: Rc<RefCell<Layout>>,
    loop_: Rc<MainLoop>,
    gpu: Rc<Gpu>,
    signals: Signals,
    stdin: Stdin,
    flavor: Flavor,
    mode: Mode,
    auto: bool,
    pause: bool,
    hook_direct: bool,
    fallback: &'static str,
    held: Option<Held>,
    desired: u32,
    offered: u32,
    negotiated: Option<u32>,
    hook_fps: Option<u32>,
    streaming: bool,
    stopping: bool,
    stopped: Option<Instant>,
    next: Option<Instant>,
    sequence: u64,
    frames: u64,
    dropped: u64,
    changes: u64,
    retired: u64,
    fenced: u64,
    relay_frames: u64,
    releases: u64,
    relay_retries: u64,
}
impl Actor {
    fn new(options: Options) -> Result<Self> {
        // SAFETY: read-only validation before adopting an inherited descriptor;
        // this process is still single threaded, so its number cannot be reused.
        if unsafe { libc::fcntl(options.fd, libc::F_GETFD) } < 0 {
            return Err("invalid-hook-socket");
        }
        // SAFETY: this dedicated process adopts the caller-transferred inherited
        // fd before creating threads; Peer validates socket type and same UID.
        let socket = unsafe { OwnedFd::from_raw_fd(options.fd) };
        let peer = Peer::new(socket, options.flavor).map_err(|_| "invalid-hook-socket")?;
        let signals = Signals::new().map_err(|_| "capture-loop-failed")?;
        let stdin = Stdin::new().map_err(|_| "capture-control-invalid")?;
        let gpu = options.gpu().ok_or("unsupported-buffer")?;
        let loop_ = MainLoop::new().map_err(|_| "pipewire-unavailable")?;
        let mut mode = options.mode;
        let mut fallback = if options.flavor == Flavor::Obs {
            "upstream-implicit-sync"
        } else {
            ""
        };
        if mode == Mode::Relay && !Publisher::supported(gpu.device()) {
            if !options.auto {
                return Err("unsupported-buffer");
            }
            mode = Mode::Copy;
            fallback = "synchronization-unavailable";
        }
        let layout = Rc::new(RefCell::new(Layout {
            texture: None,
            mode,
            gpu: gpu.clone(),
            loop_: loop_.clone(),
            size: None,
        }));
        let allocation = layout.clone();
        // SAFETY: factory writes validated native plane records synchronously,
        // retains all GPU/descriptor owners in Storage and dispatches no loop.
        let driver=unsafe{Driver::new(loop_.clone(),c"DPE game capture",c"{ media.class = Video/Source node.name = dpe-game-capture node.description = \"DPE game capture\" }",16,move|native,_|allocate(&allocation,native))}.map_err(|_|"pipewire-unavailable")?;
        let mut actor = Self {
            driver,
            peer,
            layout,
            loop_,
            gpu,
            signals,
            stdin,
            flavor: options.flavor,
            mode,
            auto: options.auto,
            pause: options.pause,
            hook_direct: mode == Mode::Relay,
            fallback,
            held: None,
            desired: options.fps,
            offered: options.fps,
            negotiated: None,
            hook_fps: None,
            streaming: false,
            stopping: false,
            stopped: None,
            next: None,
            sequence: 0,
            frames: 0,
            dropped: 0,
            changes: 0,
            retired: 0,
            fenced: 0,
            relay_frames: 0,
            releases: 0,
            relay_retries: 0,
        };
        actor.control()?;
        Ok(actor)
    }
    fn texture(&self) -> Option<Rc<Texture>> {
        self.layout.borrow().texture.clone()
    }
    fn control(&mut self) -> Result<()> {
        let mut fps = self.desired.min(self.negotiated.unwrap_or(240));
        if self.pause && self.texture().is_some() && (!self.streaming || self.negotiated.is_none())
        {
            fps = 0;
        }
        if self.hook_fps == Some(fps) {
            return Ok(());
        }
        if self.flavor == Flavor::Obs {
            if self.hook_fps.is_none() {
                let mut bytes = [0; 32];
                bytes[0] = 1;
                bytes[4..20].copy_from_slice(&self.gpu.uuid());
                self.peer.send(&bytes).map_err(|_| "hook-control-failed")?;
            }
        } else {
            let packet = Packet {
                flags: hook::CAPTURE | if self.hook_direct { hook::DIRECT } else { 0 },
                fps,
                uuid: self.gpu.uuid(),
                ..Packet::new(hook::CONTROL)
            };
            self.peer
                .send(&packet.bytes())
                .map_err(|_| "hook-control-failed")?;
        }
        self.hook_fps = Some(fps);
        self.next = None;
        Ok(())
    }
    fn acknowledge(&self, frame: &Packet) -> Result<()> {
        if self.flavor == Flavor::Obs
            || self.stopped.is_some()
            || self
                .texture()
                .is_none_or(|texture| texture.packet.generation != frame.generation)
        {
            return Ok(());
        }
        self.peer
            .send(
                &Packet {
                    generation: frame.generation,
                    sequence: frame.sequence,
                    ..Packet::new(hook::RELEASE)
                }
                .bytes(),
            )
            .map_err(|_| "hook-release-failed")
    }
    fn offer(&self) -> Result<()> {
        let texture = self.texture().ok_or("missing-game-texture")?;
        let format = if self.mode == Mode::Relay {
            spa(texture.packet.format).ok_or("unsupported-buffer")?
        } else {
            f::spa_video_format_SPA_VIDEO_FORMAT_BGRA
        };
        let modifier = if self.mode == Mode::Relay {
            texture.packet.modifier
        } else {
            0
        };
        let pod = source_pods::offer(
            Format {
                width: texture.packet.width,
                height: texture.packet.height,
                format,
                modifier,
            },
            self.offered,
        )
        .ok_or("unsupported-buffer")?;
        self.driver.offer(&pod).map_err(|_| "pipewire-unavailable")
    }
    fn announce(&self) {
        if let (Some(texture), Some(node)) = (self.texture(), self.driver.node_id()) {
            emit(format!(
                "{{\"event\":\"ready\",\"backend\":\"vkcapture\",\"nodeId\":{node},\"width\":{},\"height\":{},\"generation\":{},\"fps\":{},\"handoff\":{},\"handoffFallback\":{}}}",
                texture.packet.width,
                texture.packet.height,
                texture.packet.generation,
                self.offered,
                quoted(if self.mode == Mode::Relay {
                    "relay"
                } else {
                    "copy"
                }),
                quoted(self.fallback)
            ));
        }
    }
    fn texture_received(&mut self, packet: Packet, descriptors: wire::Message) -> Result<()> {
        if packet.generation == 0
            || self
                .texture()
                .is_some_and(|texture| packet.generation <= texture.packet.generation)
            || packet.nfd as usize != descriptors.count
            || !(1..=4).contains(&descriptors.count)
            || descriptors.descriptors[..descriptors.count]
                .iter()
                .any(Option::is_none)
            || packet.width == 0
            || packet.height == 0
            || packet.width > 8192
            || packet.height > 8192
        {
            return Err("invalid-game-texture");
        }
        if self.mode == Mode::Relay
            && self.texture().is_none()
            && ((u32::from_ne_bytes(packet.name[..4].try_into().unwrap()) != 0
                && std::env::var_os("DISPLAY").is_some()
                && std::env::var_os("WAYLAND_DISPLAY").is_none())
                || std::env::var_os("DPE_GAME_CURSOR_TOPLEVEL").is_some())
        {
            self.mode = Mode::Copy;
            self.fallback = "cursor-composition";
        }
        let mut texture = Texture {
            packet,
            descriptors,
            imported: None,
        };
        if self.mode == Mode::Relay {
            if texture.packet.flags != 0 || spa(texture.packet.format).is_none() {
                return Err("unsupported-buffer");
            }
        } else {
            let fds = texture.fds();
            texture.imported = Some(
                self.gpu
                    .import(&texture.packet, &fds[..texture.descriptors.count])
                    .ok_or("unsupported-buffer")?,
            );
        }
        if matches!(self.held, Some(Held::Acquire { .. } | Held::Ready { .. })) {
            self.held = None;
        }
        self.stopped = None;
        self.changes += 1;
        let size = (texture.packet.width, texture.packet.height);
        let previous = self
            .layout
            .borrow()
            .texture
            .as_ref()
            .map(|texture| (texture.packet.width, texture.packet.height));
        {
            let mut layout = self.layout.borrow_mut();
            layout.texture = Some(Rc::new(texture));
            layout.mode = self.mode;
        }
        if self.mode == Mode::Relay
            || previous != Some(size)
            || self.driver.state() == f::DPE_STREAM_UNCONNECTED
        {
            self.offer()?;
        } else {
            self.announce();
        }
        self.control()
    }
    fn frame_received(&mut self, packet: Packet, mut descriptors: wire::Message) -> Result<()> {
        let texture = self.texture().ok_or("missing-game-texture")?;
        let explicit = packet.flags == hook::SYNC_FD;
        if packet.generation != texture.packet.generation
            || self.stopped.is_some()
            || packet.sequence <= self.sequence
            || packet.pts == 0
            || packet.nfd as usize != descriptors.count
            || descriptors.count != usize::from(explicit)
            || (!explicit && packet.flags != 0)
            || self
                .held
                .as_ref()
                .is_some_and(|held| held.frame().packet.generation == packet.generation)
        {
            return Err("invalid-game-frame");
        }
        self.sequence = packet.sequence;
        let fence = if explicit {
            self.fenced += 1;
            let fd = descriptors.descriptors[0]
                .take()
                .ok_or("invalid-game-fence")?;
            game_capture::fence_complete(fd.as_fd()).map_err(|_| "invalid-game-fence")?;
            Some(fd)
        } else {
            None
        };
        if self.held.is_some() || !self.streaming || self.negotiated.is_none() {
            self.dropped += 1;
            self.acknowledge(&packet)?;
            return Ok(());
        }
        let frame = Frame { packet, texture };
        self.held = Some(match (self.mode, fence) {
            (Mode::Copy, Some(fence)) => Held::Acquire { frame, fence },
            (_, acquire) => Held::Ready { frame, acquire },
        });
        self.process()
    }
    fn receive(&mut self) -> Result<()> {
        for _ in 0..4 {
            let Some(Received {
                packet,
                descriptors,
            }) = self.peer.receive().map_err(|_| "hook-disconnected")?
            else {
                break;
            };
            match packet.kind {
                hook::TEXTURE => self.texture_received(packet, descriptors)?,
                hook::FRAME if self.flavor == Flavor::Dpe => {
                    self.frame_received(packet, descriptors)?
                }
                hook::STOP
                    if self.flavor == Flavor::Dpe
                        && descriptors.count == 0
                        && packet.nfd == 0
                        && packet.flags == 0
                        && self.texture().is_some_and(|texture| {
                            texture.packet.generation == packet.generation
                        }) =>
                {
                    if matches!(self.held, Some(Held::Acquire { .. } | Held::Ready { .. })) {
                        self.held = None;
                    }
                    self.stopped = Some(Instant::now() + Duration::from_secs(5));
                }
                hook::UNSUPPORTED
                    if self.mode == Mode::Relay
                        && self.texture().is_none()
                        && descriptors.count == 0
                        && packet.nfd == 0
                        && packet.flags == 0
                        && packet.generation == 0 =>
                {
                    if !self.auto {
                        return Err("unsupported-buffer");
                    }
                    self.mode = Mode::Copy;
                    self.hook_direct = false; // No allocation exists yet; change its contract once.
                    self.layout.borrow_mut().mode = Mode::Copy;
                    self.fallback = "export-unavailable";
                    self.hook_fps = None;
                    self.control()?;
                }
                _ => return Err("unexpected-hook-packet"),
            }
        }
        Ok(())
    }
    fn process(&mut self) -> Result<()> {
        let Some((frame, acquire)) = Held::take_ready(&mut self.held) else {
            return Ok(());
        };
        if !self.streaming
            || self.negotiated.is_none()
            || self.stopped.is_some()
            || self
                .texture()
                .is_none_or(|texture| texture.packet.generation != frame.packet.generation)
        {
            self.dropped += 1;
            return self.acknowledge(&frame.packet);
        }
        let Some(buffer) = self.driver.dequeue().map_err(|_| "pipewire-unavailable")? else {
            self.dropped += 1;
            return self.acknowledge(&frame.packet);
        };
        match buffer.storage() {
            Storage::Copy(surface) => {
                let imported = frame
                    .texture
                    .imported
                    .as_ref()
                    .ok_or("unsupported-buffer")?;
                if imported.dimensions() != (surface.export().width, surface.export().height) {
                    self.driver
                        .return_unused(&buffer)
                        .map_err(|_| "pipewire-unavailable")?;
                    self.dropped += 1;
                    return self.acknowledge(&frame.packet);
                }
                let fence = surface.render(imported, true).ok_or("gpu-copy-failed")?;
                self.held = Some(Held::Copy {
                    frame,
                    buffer,
                    fence,
                });
            }
            Storage::Relay {
                publisher,
                generation,
            } => {
                if *generation != frame.packet.generation {
                    self.driver
                        .return_unused(&buffer)
                        .map_err(|_| "pipewire-unavailable")?;
                    self.dropped += 1;
                    return self.acknowledge(&frame.packet);
                }
                buffer
                    .set_header(frame.packet.pts, frame.packet.sequence)
                    .map_err(|_| "invalid-frame-header")?;
                buffer
                    .set_relay_ready(frame.texture.packet.nfd)
                    .map_err(|_| "invalid-frame-chunks")?;
                // SAFETY: this exact native buffer is live and checked out.
                // Fenced FRAME transfers the real completion sync-file; plain
                // DPE FRAME is emitted only after GL/Vulkan completion (including
                // Vulkan's already-signaled sentinel), never OBS timer readiness.
                unsafe {
                    publisher.submit_optional(acquire)?;
                }
                self.held = Some(Held::Relay {
                    frame,
                    buffer: buffer.clone(),
                    retry: Instant::now() + Duration::from_millis(100),
                });
                self.driver
                    .queue(&buffer)
                    .map_err(|_| "pipewire-queue-failed")?;
                self.driver.trigger().map_err(|_| "pipewire-unavailable")?;
                self.frames += 1;
                self.relay_frames += 1;
            }
        }
        Ok(())
    }
    fn complete(&mut self) -> Result<()> {
        let Some(held) = self.held.take() else {
            return Ok(());
        };
        match held {
            Held::Acquire { frame, fence } => {
                if !game_capture::fence_complete(fence.as_fd())
                    .map_err(|_| "game-completion-failed")?
                {
                    self.held = Some(Held::Acquire { frame, fence });
                } else {
                    self.held = Some(Held::Ready {
                        frame,
                        acquire: None,
                    });
                    self.process()?;
                }
            }
            Held::Copy {
                frame,
                buffer,
                fence,
            } => {
                if !game_capture::fence_complete(fence.as_fd()).map_err(|_| "gpu-copy-failed")? {
                    self.held = Some(Held::Copy {
                        frame,
                        buffer,
                        fence,
                    });
                    return Ok(());
                }
                let current = self.stopped.is_none()
                    && self.texture().is_some_and(|texture| {
                        texture.packet.generation == frame.packet.generation
                    })
                    && self.streaming
                    && self.negotiated.is_some();
                if buffer.live() {
                    if current {
                        let Storage::Copy(surface) = buffer.storage() else {
                            return Err("copy-owner-mismatch");
                        };
                        let export = surface.export();
                        buffer
                            .set_copy_size(
                                export
                                    .stride
                                    .checked_mul(export.height)
                                    .ok_or("copy-extent-overflow")?,
                            )
                            .map_err(|_| "invalid-copy-buffer")?;
                        buffer
                            .set_header(frame.packet.pts, frame.packet.sequence)
                            .map_err(|_| "invalid-frame-header")?;
                        self.driver
                            .queue(&buffer)
                            .map_err(|_| "pipewire-queue-failed")?;
                        self.driver.trigger().map_err(|_| "pipewire-unavailable")?;
                        self.frames += 1;
                    } else {
                        self.driver
                            .return_unused(&buffer)
                            .map_err(|_| "pipewire-unavailable")?;
                        self.dropped += 1;
                    }
                } else {
                    self.retired += 1;
                    self.dropped += 1;
                }
                self.acknowledge(&frame.packet)?;
            }
            Held::Relay {
                frame,
                buffer,
                retry,
            } => {
                let Storage::Relay { publisher, .. } = buffer.storage() else {
                    return Err("relay-owner-mismatch");
                };
                match publisher.take_completion() {
                    None => {
                        self.held = Some(Held::Relay {
                            frame,
                            buffer,
                            retry,
                        })
                    }
                    Some(false) => return Err("consumer-release-failed"),
                    Some(true) => {
                        self.releases += 1;
                        if !buffer.live() {
                            self.retired += 1;
                        }
                        self.acknowledge(&frame.packet)?;
                    }
                }
            }
            held @ Held::Ready { .. } => self.held = Some(held),
        }
        Ok(())
    }
    fn events(&mut self) -> Result<()> {
        while let Some(event) = self.driver.next_event() {
            match event {
                Event::State { next, error, .. } => {
                    self.streaming = next == f::pw_stream_state_PW_STREAM_STATE_STREAMING;
                    if next == f::DPE_STREAM_ERROR {
                        eprintln!(
                            "DPE game PipeWire: {}",
                            error.as_deref().unwrap_or("stream failed")
                        );
                        return Err("pipewire-unavailable");
                    }
                    self.control()?;
                    if next == f::pw_stream_state_PW_STREAM_STATE_PAUSED {
                        self.announce();
                    }
                }
                Event::Format(None) => {
                    self.negotiated = None;
                    self.control()?;
                }
                Event::Format(Some(pod)) => {
                    let view = pod.format();
                    let texture = self.texture().ok_or("missing-game-texture")?;
                    let size = view.dimensions().ok_or("invalid-game-format")?;
                    if size != (texture.packet.width, texture.packet.height) {
                        continue;
                    }
                    let format = if self.mode == Mode::Relay {
                        spa(texture.packet.format).ok_or("unsupported-buffer")?
                    } else {
                        f::spa_video_format_SPA_VIDEO_FORMAT_BGRA
                    };
                    let modifier = if self.mode == Mode::Relay {
                        texture.packet.modifier
                    } else {
                        0
                    };
                    if !view.is_raw()
                        || view.pixel_format() != Some(format)
                        || view.modifier() != Some(modifier)
                        || size.0 == 0
                        || size.1 == 0
                        || size.0 > 8192
                        || size.1 > 8192
                    {
                        return Err("invalid-game-format");
                    }
                    let rate = view
                        .rate()
                        .filter(|rate| rate.numerator > 0)
                        .or_else(|| view.maximum())
                        .filter(|rate| {
                            rate.denominator > 0
                                && rate.numerator >= rate.denominator
                                && u64::from(rate.numerator) <= 240 * u64::from(rate.denominator)
                        })
                        .ok_or("invalid-game-rate")?;
                    self.negotiated = Some(rate.numerator / rate.denominator);
                    self.layout.borrow_mut().size = Some(size);
                    self.control()?;
                    let pods = source_pods::buffers(
                        size.0,
                        size.1,
                        if self.mode == Mode::Relay {
                            Some(texture.packet.nfd)
                        } else {
                            None
                        },
                    )
                    .ok_or("invalid-game-format")?;
                    self.driver
                        .buffers(&pods)
                        .map_err(|_| "pipewire-unavailable")?;
                    self.announce();
                }
                Event::Process => self.process()?,
                Event::Failed(reason) => {
                    eprintln!("DPE game source: {reason}");
                    return Err("pipewire-unavailable");
                }
                Event::Added(_) | Event::Removed(_) => (),
            }
        }
        Ok(())
    }
    fn run(&mut self) -> Result<()> {
        while !self.stopping {
            self.loop_.iterate(0).map_err(|_| "pipewire-unavailable")?;
            self.events()?;
            if matches!(self.held, Some(Held::Relay { .. })) {
                self.complete()?;
            }
            if self.held.is_none() && self.offered != self.desired && self.texture().is_some() {
                self.offered = self.desired;
                self.offer()?;
            }
            let mut timeout = self
                .stopped
                .map(|deadline| deadline.saturating_duration_since(Instant::now()));
            if timeout.is_some_and(|delay| delay.is_zero()) {
                return Err("game-surface-closed");
            }
            if self.streaming
                && let Some(Held::Relay { retry, .. }) = &mut self.held
            {
                let now = Instant::now();
                if now >= *retry {
                    // A driver trigger can race graph startup. Retry only the
                    // already-queued lease: never overwrite or release storage
                    // before the consumer signals its real release point.
                    self.driver.trigger().map_err(|_| "pipewire-unavailable")?;
                    self.relay_retries += 1;
                    *retry = now + Duration::from_millis(100);
                }
                let delay = retry.saturating_duration_since(Instant::now());
                timeout = Some(timeout.map_or(delay, |limit| limit.min(delay)));
            }
            if self.flavor == Flavor::Obs
                && self.streaming
                && self.negotiated.is_some()
                && self.held.is_none()
                && let Some(texture) = self.texture()
            {
                let now = Instant::now();
                let deadline = self.next.get_or_insert(now);
                let period = Duration::from_nanos(
                    1_000_000_000 / u64::from(self.desired.min(self.negotiated.unwrap_or(240))),
                );
                if now >= *deadline {
                    *deadline = now + period;
                    self.sequence = self
                        .sequence
                        .checked_add(1)
                        .ok_or("game-sequence-exhausted")?;
                    let mut time = libc::timespec {
                        tv_sec: 0,
                        tv_nsec: 0,
                    };
                    // SAFETY: initialized local output for the protocol's shared
                    // CLOCK_MONOTONIC timestamp; no retained foreign storage.
                    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } != 0 {
                        return Err("capture-clock-failed");
                    }
                    let timestamp = u64::try_from(time.tv_sec)
                        .ok()
                        .and_then(|seconds| seconds.checked_mul(1_000_000_000))
                        .and_then(|nanos| nanos.checked_add(time.tv_nsec as u64))
                        .ok_or("capture-clock-failed")?;
                    let packet = Packet {
                        generation: texture.packet.generation,
                        sequence: self.sequence,
                        pts: timestamp,
                        ..Packet::new(hook::FRAME)
                    };
                    self.held = Some(Held::Ready {
                        frame: Frame { packet, texture },
                        acquire: None,
                    });
                }
                let delay = deadline.saturating_duration_since(Instant::now());
                timeout = Some(timeout.map_or(delay, |limit| limit.min(delay)));
            }
            if matches!(self.held, Some(Held::Ready { .. })) {
                self.process()?;
            }
            if self.driver.has_events() {
                timeout = Some(Duration::ZERO);
            }
            let fence = self.held.as_ref().map_or(-1, Held::fence);
            let mut polls = [
                libc::pollfd {
                    fd: self.loop_.fd(),
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: self.peer.descriptor().as_raw_fd(),
                    events: libc::POLLIN,
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
                libc::pollfd {
                    fd: fence,
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
            // SAFETY: all descriptors and the optional timeout remain retained
            // across this bounded non-dispatch wait on the owning thread.
            if unsafe { libc::ppoll(polls.as_mut_ptr(), polls.len() as _, time, std::ptr::null()) }
                < 0
            {
                if std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
                    continue;
                }
                return Err("capture-loop-failed");
            }
            if polls[3].revents != 0 {
                return Ok(());
            }
            if polls[2].revents != 0 {
                let stopping = &mut self.stopping;
                let desired = &mut self.desired;
                if !self
                    .stdin
                    .read(|command| {
                        if !*stopping {
                            match command {
                                Command::Stop => *stopping = true,
                                Command::Rate(value) => *desired = value,
                            }
                        }
                    })
                    .map_err(|_| "capture-control-invalid")?
                    || self.stopping
                {
                    return Ok(());
                }
                self.control()?;
            }
            if polls[1].revents != 0 {
                if polls[1].revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
                    return Err("hook-disconnected");
                }
                self.receive()?;
            }
            if polls[4].revents != 0 {
                self.complete()?;
            }
        }
        Ok(())
    }
}
impl Drop for Actor {
    fn drop(&mut self) {
        self.peer.disconnect();
        drop(self.held.take());
    }
}
fn main() {
    let Some(options) = Options::parse(std::env::args().skip(1)) else {
        std::process::exit(2);
    };
    let mut actor = match Actor::new(options) {
        Ok(actor) => actor,
        Err(reason) => {
            emit(format!(
                "{{\"event\":\"ended\",\"backend\":\"vkcapture\",\"failed\":true,\"errorCode\":{}}}",
                quoted(reason)
            ));
            std::process::exit(3);
        }
    };
    let result = actor.run();
    if let Err(reason) = &result {
        eprintln!("DPE game capture: {reason}");
    }
    let report = format!(
        "{{\"event\":\"ended\",\"backend\":\"vkcapture\",\"frames\":{},\"failed\":{},\"errorCode\":{},\"dropped\":{},\"textureChanges\":{},\"retiredBuffers\":{},\"fencedFrames\":{},\"handoff\":{},\"handoffFallback\":{},\"relayFrames\":{},\"relayReleases\":{},\"relayRetries\":{}}}",
        actor.frames,
        result.is_err(),
        quoted(result.err().unwrap_or("")),
        actor.dropped,
        actor.changes,
        actor.retired,
        actor.fenced,
        quoted(if actor.mode == Mode::Relay {
            "relay"
        } else {
            "copy"
        }),
        quoted(actor.fallback),
        actor.relay_frames,
        actor.releases,
        actor.relay_retries
    );
    drop(actor);
    emit(report);
    if result.is_err() {
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unexpected_process_notifications_retain_the_pending_acquire_and_texture_lease() {
        let texture = Rc::new(Texture {
            packet: Packet::new(hook::TEXTURE),
            descriptors: wire::Message {
                descriptors: std::array::from_fn(|_| None),
                count: 0,
            },
            imported: None,
        });
        let fence: OwnedFd = std::fs::File::open("/dev/null").unwrap().into();
        let raw = fence.as_raw_fd();
        let mut held = Some(Held::Acquire {
            frame: Frame {
                packet: Packet::new(hook::FRAME),
                texture: texture.clone(),
            },
            fence,
        });
        for _ in 0..1024 {
            assert!(Held::take_ready(&mut held).is_none());
        }
        assert_eq!(Rc::strong_count(&texture), 2);
        // SAFETY: the pending state still owns this exact descriptor; querying
        // it proves coalesced process callbacks did not retire the acquire.
        assert!(unsafe { libc::fcntl(raw, libc::F_GETFD) } >= 0);
        let Some(Held::Acquire { frame, fence }) = held.take() else {
            panic!("lost acquire lease");
        };
        drop(fence);
        held = Some(Held::Ready {
            frame,
            acquire: None,
        });
        let (frame, acquire) = Held::take_ready(&mut held).unwrap();
        assert!(held.is_none() && acquire.is_none());
        drop(frame);
        assert_eq!(Rc::strong_count(&texture), 1);
    }
}
