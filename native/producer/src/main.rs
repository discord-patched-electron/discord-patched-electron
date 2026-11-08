// SPDX-License-Identifier: GPL-3.0-only
//! Same-user direct capture producer. No image readback, PipeWire connection or
//! GPU encoding lives here; descriptor leases and explicit fences cross IPC.
// Experimental vkcapture cursor support is currently unsupported.
// mod cursor;
mod game;
mod system;
#[allow(
    non_camel_case_types,
    non_snake_case,
    non_upper_case_globals,
    dead_code,
    clippy::all,
    clippy::undocumented_unsafe_blocks
)]
mod ffi {
    include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
}
use dpe_core::direct::{self, Image, Packet};
use dpe_kms::capture;
use dpe_os::{
    profiler::{self, Span},
    wire,
};
use std::{
    ffi::CString,
    io::{self, Write},
    os::fd::{AsFd, AsRawFd, FromRawFd, OwnedFd},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};
use system::{Descriptors, Endpoint};
static STOPPED: AtomicBool = AtomicBool::new(false);
extern "C" fn stop(_: libc::c_int) {
    STOPPED.store(true, Ordering::Relaxed);
}
fn signals() -> io::Result<()> {
    // SAFETY: plain C signal action, empty mask and non-restarting handler so ppoll returns promptly.
    let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
    action.sa_sigaction = stop as *const () as usize;
    // SAFETY: initialized signal mask output and fixed process-local signal handlers, installed before capture starts.
    unsafe {
        libc::sigemptyset(&mut action.sa_mask);
        if libc::sigaction(libc::SIGTERM, &action, std::ptr::null_mut()) != 0
            || libc::sigaction(libc::SIGINT, &action, std::ptr::null_mut()) != 0
        {
            return Err(io::Error::last_os_error());
        }
        action.sa_sigaction = libc::SIG_IGN;
        if libc::sigaction(libc::SIGPIPE, &action, std::ptr::null_mut()) != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}
struct Config {
    hook: Option<i32>,
    hook_pause: bool,
    uuid: Option<[u8; 16]>,
    output: Option<CString>,
    render: CString,
    fps: u32,
}
impl Config {
    fn parse(arguments: impl IntoIterator<Item = std::ffi::OsString>) -> Option<Self> {
        let mut config = Self {
            hook: None,
            hook_pause: false,
            uuid: None,
            output: None,
            render: CString::new("auto").ok()?,
            fps: 60,
        };
        let mut arguments = arguments.into_iter();
        while let Some(key) = arguments.next() {
            let value = arguments.next()?;
            let text = value.to_str()?;
            match key.to_str()? {
                "--hook-fd" => {
                    config.hook = Some(decimal(text)?.try_into().ok()?);
                }
                "--hook-pause" if text == "yes" => config.hook_pause = true,
                "--device-uuid" => {
                    if text.len() != 32 || !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                        return None;
                    }
                    let mut uuid = [0; 16];
                    for (i, byte) in uuid.iter_mut().enumerate() {
                        *byte = u8::from_str_radix(&text[2 * i..2 * i + 2], 16).ok()?;
                    }
                    config.uuid = Some(uuid);
                }
                "--output" => {
                    config.output = Some(CString::new(text).ok()?);
                }
                "--render-node" => {
                    config.render = CString::new(text).ok()?;
                }
                "--fps" => {
                    config.fps = decimal(text)?;
                    if !(1..=240).contains(&config.fps) {
                        return None;
                    }
                }
                // Permission is always checked by the shared KMS session. This
                // compatibility option cannot grant or bypass the saved permission.
                "--authorize" if text == "true" => {}
                _ => return None,
            }
        }
        if config.hook.is_some() == config.output.is_some()
            || config.hook.is_some() && config.uuid.is_none()
        {
            return None;
        }
        Some(config)
    }
}
fn decimal(text: &str) -> Option<u32> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        None
    } else {
        text.parse::<u32>().ok().filter(|v| *v <= i32::MAX as u32)
    }
}
struct Commands {
    bytes: [u8; 64],
    length: usize,
}
impl Default for Commands {
    fn default() -> Self {
        Self {
            bytes: [0; 64],
            length: 0,
        }
    }
}
impl Commands {
    fn push(&mut self, byte: u8) -> io::Result<Option<u32>> {
        if byte == b'\n' {
            let fps = dpe_core::control::rate(&self.bytes[..self.length]).ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidData, "invalid capture rate command")
            })?;
            self.length = 0;
            Ok(Some(fps))
        } else if byte != 0 && self.length < self.bytes.len() - 1 {
            self.bytes[self.length] = byte;
            self.length += 1;
            Ok(None)
        } else {
            Err(io::ErrorKind::InvalidData.into())
        }
    }
}
#[derive(Clone, Copy)]
struct Lease {
    generation: u64,
    sequence: u64,
}
enum Backend {
    Kms(Box<capture::Capture>),
    Game(Box<game::Game>),
}
impl Backend {
    fn control(&self, fps: u32) -> io::Result<()> {
        match self {
            Self::Game(game) => game.control(fps),
            Self::Kms(_) => Ok(()),
        }
    }
    fn release(&self, lease: Lease) -> io::Result<()> {
        match self {
            Self::Game(game) => game.release(lease.generation, lease.sequence),
            Self::Kms(_) => Ok(()),
        }
    }
}
struct Peer {
    socket: OwnedFd,
    started: bool,
}
struct Source {
    endpoint: Endpoint,
    backend: Backend,
    peer: Option<Peer>,
    fps: u32,
    held: Option<Lease>,
    next: Option<Instant>,
    changed: Option<Instant>,
    sequence: u64,
    delivered: u64,
    dropped: u64,
    commands: Commands,
}
impl Drop for Source {
    fn drop(&mut self) {
        // Wake the native consumer before releasing or waiting on source owners.
        drop(self.peer.take());
    }
}
fn frame_image<'a>(
    image: capture::Image<'a>,
    descriptors: &mut Descriptors<'a>,
) -> io::Result<Image> {
    let mut output = Image {
        width: image.width,
        height: image.height,
        format: image.format,
        modifier: image.modifier,
        source: image.source,
        destination: image.destination,
        ..Default::default()
    };
    for (i, plane) in image.planes.into_iter().flatten().enumerate() {
        output.fd[i] = descriptors.push(plane.descriptor)?;
        output.stride[i] = plane.stride;
        output.offset[i] = plane.offset;
        output.planes += 1;
    }
    Ok(output)
}
fn now_ns() -> io::Result<u64> {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: initialized timespec output for the fixed monotonic Linux clock.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok((time.tv_sec as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add(time.tv_nsec as u64))
}
fn escaped(text: &str) -> String {
    let mut output = String::from("\"");
    for ch in text.chars() {
        match ch {
            '"' | '\\' => {
                output.push('\\');
                output.push(ch)
            }
            ch if ch < ' ' => {
                use std::fmt::Write;
                let _ = write!(output, "\\u{:04x}", ch as u32);
            }
            ch => output.push(ch),
        }
    }
    output.push('"');
    output
}
impl Source {
    fn new(config: Config) -> io::Result<Self> {
        let backend = if let Some(raw) = config.hook {
            // SAFETY: query only. A successful inherited descriptor is transferred once from the invoking broker.
            if unsafe { libc::fcntl(raw, libc::F_GETFD) } < 0 {
                return Err(io::Error::last_os_error());
            }
            // SAFETY: CLI transfers this inherited descriptor to the process; no other Rust owner exists.
            let hook = unsafe { OwnedFd::from_raw_fd(raw) };
            Backend::Game(Box::new(game::Game::new(
                hook,
                config.uuid.ok_or(io::ErrorKind::InvalidInput)?,
                &config.render,
                config.hook_pause,
            )?))
        } else {
            let kms = capture::Capture::open(
                config
                    .output
                    .as_deref()
                    .ok_or(io::ErrorKind::InvalidInput)?,
                &config.render,
            )
            .map_err(io::Error::other)?;
            Backend::Kms(Box::new(kms))
        };
        Ok(Self {
            endpoint: Endpoint::new()?,
            backend,
            peer: None,
            fps: config.fps,
            held: None,
            next: None,
            changed: None,
            sequence: 0,
            delivered: 0,
            dropped: 0,
            commands: Commands::default(),
        })
    }
    fn name(&self) -> &str {
        match self.backend {
            Backend::Game(_) => "vkcapture",
            Backend::Kms(_) => "kms",
        }
    }
    fn ready(&self) -> io::Result<()> {
        let (width, height) = match &self.backend {
            Backend::Kms(kms) => kms.dimensions(),
            Backend::Game(_) => (0, 0),
        };
        let path = self
            .endpoint
            .path
            .to_str()
            .ok_or(io::ErrorKind::InvalidData)?;
        let mut out = io::stdout().lock();
        writeln!(
            out,
            "{{\"event\":\"ready\",\"backend\":\"{}\",\"transport\":\"direct-gpu\",\"handoff\":\"direct-gpu\",\"nodeId\":{},\"width\":{},\"height\":{},\"socketPath\":{}}}",
            self.name(),
            direct::NODE,
            width,
            height,
            escaped(path)
        )?;
        out.flush()
    }
    fn client_input(&mut self) -> io::Result<bool> {
        let peer = self.peer.as_ref().ok_or(io::ErrorKind::NotConnected)?;
        let mut bytes = [0; direct::SIZE];
        let message = match wire::receive(peer.socket.as_fd(), &mut bytes, 0) {
            Ok(Some(message)) => message,
            Ok(None) => return Ok(true),
            Err(error)
                if error.kind() == io::ErrorKind::UnexpectedEof
                    || system::consumer_closed(&error) =>
            {
                // A selected but unstarted native request may be cancelled and
                // replaced. Only a started stream closes the source on EOF.
                if peer.started {
                    return Ok(false);
                }
                self.peer = None;
                return Ok(true);
            }
            Err(error) => return Err(error),
        };
        let packet = Packet::parse(&bytes)
            .filter(|packet| packet.descriptors as usize == message.count)
            .ok_or(io::ErrorKind::InvalidData)?;
        match packet.kind {
            direct::START | direct::RATE
                if packet.fps <= 240
                    && ((packet.kind == direct::START && !peer.started)
                        || (packet.kind == direct::RATE && peer.started)) =>
            {
                self.fps = packet.fps;
                self.next = None;
                self.peer.as_mut().expect("peer retained").started = true;
                self.endpoint.server = None;
                self.backend.control(self.fps)?;
            }
            direct::RELEASE => {
                let lease = self.held.ok_or(io::ErrorKind::InvalidData)?;
                if (lease.generation, lease.sequence) != (packet.generation, packet.sequence) {
                    return Err(io::ErrorKind::InvalidData.into());
                }
                self.backend.release(lease)?;
                self.held = None;
            }
            _ => return Err(io::ErrorKind::InvalidData.into()),
        }
        Ok(true)
    }
    fn kms_frame(&mut self) -> io::Result<bool> {
        let Backend::Kms(kms) = &mut self.backend else {
            return Ok(true);
        };
        let frame = match kms.next(|| STOPPED.load(Ordering::Relaxed)) {
            Err(capture::Error::Cancelled) => return Ok(true),
            result => result.map_err(io::Error::other)?,
        };
        let Some(frame) = frame else {
            self.dropped += 1;
            if self.changed.get_or_insert_with(Instant::now).elapsed() >= Duration::from_secs(2) {
                return Err(io::ErrorKind::InvalidData.into());
            }
            return Ok(true);
        };
        self.changed = None;
        let (width, height) = kms.dimensions();
        let (device_major, device_minor) = kms.device();
        let primary = frame.primary();
        let first = primary.planes[0]
            .ok_or(io::ErrorKind::InvalidData)?
            .descriptor;
        let mut descriptors = Descriptors::new(first);
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or(io::ErrorKind::InvalidData)?;
        let mut packet = Packet {
            generation: 1,
            sequence: self.sequence,
            timestamp_ns: now_ns()?,
            width,
            height,
            device_major,
            device_minor,
            ..Packet::new(direct::FRAME)
        };
        packet.image = frame_image(primary, &mut descriptors)?;
        if let Some(cursor) = frame.cursor() {
            packet.cursor = frame_image(cursor, &mut descriptors)?;
        }
        let acquire = system::fences(descriptors.as_slice())?;
        packet.acquire = descriptors.push(acquire.as_fd())? + 1;
        packet.descriptors = descriptors.as_slice().len() as u32;
        let _profile = Span::new(17);
        if let Err(error) = wire::send(
            self.peer
                .as_ref()
                .ok_or(io::ErrorKind::NotConnected)?
                .socket
                .as_fd(),
            &packet.bytes(),
            descriptors.as_slice(),
        ) {
            if system::consumer_closed(&error) {
                return Ok(false);
            }
            return Err(error);
        }
        self.held = Some(Lease {
            generation: 1,
            sequence: self.sequence,
        });
        self.delivered += 1;
        Ok(true)
    }
    fn run(&mut self) -> io::Result<()> {
        self.ready()?;
        let deadline = Instant::now() + Duration::from_secs(90);
        while !STOPPED.load(Ordering::Relaxed) {
            let now = Instant::now();
            let Some(peer) = self.peer.as_ref() else {
                if now > deadline {
                    return Err(io::ErrorKind::TimedOut.into());
                }
                let server = self
                    .endpoint
                    .server
                    .as_ref()
                    .ok_or(io::ErrorKind::NotConnected)?;
                let mut wait = [libc::pollfd {
                    fd: server.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                }];
                if let Err(error) = system::poll(&mut wait, Duration::from_millis(100)) {
                    if error.kind() == io::ErrorKind::Interrupted {
                        continue;
                    }
                    return Err(error);
                }
                if wait[0].revents & libc::POLLIN != 0 {
                    self.peer = Some(Peer {
                        socket: self.endpoint.accept()?,
                        started: false,
                    });
                }
                continue;
            };
            let started = peer.started;
            if let Backend::Game(game) = &self.backend
                && game.stop_deadline.is_some_and(|deadline| now > deadline)
            {
                return Err(io::ErrorKind::UnexpectedEof.into());
            }
            if matches!(self.backend, Backend::Kms(_))
                && started
                && self.fps != 0
                && self.held.is_none()
                && self.next.is_none_or(|next| now >= next)
            {
                let period = Duration::from_nanos(1_000_000_000 / u64::from(self.fps));
                self.next = Some(next_deadline(self.next, now, period));
                if !self.kms_frame()? {
                    break;
                }
                if STOPPED.load(Ordering::Relaxed) {
                    break;
                }
            }
            let peer = self.peer.as_ref().expect("capture cannot remove its peer");
            let mut wait = [
                libc::pollfd {
                    fd: peer.socket.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: match &self.backend {
                        Backend::Game(game) => game.socket.as_raw_fd(),
                        Backend::Kms(_) => -1,
                    },
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: 0,
                    events: libc::POLLIN,
                    revents: 0,
                },
            ];
            let timeout = if matches!(self.backend, Backend::Kms(_))
                && started
                && self.fps != 0
                && self.held.is_none()
            {
                self.next
                    .unwrap_or(now)
                    .saturating_duration_since(Instant::now())
            } else {
                Duration::from_millis(100)
            };
            if let Err(error) = system::poll(&mut wait, timeout) {
                if error.kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(error);
            }
            if wait[0].revents & libc::POLLIN != 0 && !self.client_input()? {
                break;
            }
            if self.peer.is_none() {
                continue;
            }
            if wait[0].revents & (libc::POLLHUP | libc::POLLERR | libc::POLLNVAL) != 0 {
                break;
            }
            if let Backend::Game(game) = &mut self.backend {
                if wait[1].revents & libc::POLLIN != 0 {
                    match game.input(
                        self.peer.as_ref().expect("peer retained").socket.as_fd(),
                        self.fps,
                        self.held.is_some(),
                    )? {
                        game::Input::Delivered {
                            generation,
                            sequence,
                        } => {
                            self.held = Some(Lease {
                                generation,
                                sequence,
                            });
                            self.delivered += 1;
                        }
                        game::Input::Dropped => self.dropped += 1,
                        game::Input::ConsumerClosed => break,
                        _ => {}
                    }
                }
                if wait[1].revents & (libc::POLLHUP | libc::POLLERR | libc::POLLNVAL) != 0 {
                    return Err(io::ErrorKind::UnexpectedEof.into());
                }
            }
            if wait[2].revents & (libc::POLLIN | libc::POLLHUP) != 0 {
                let mut bytes = [0u8; 128];
                // SAFETY: poll marked stdin readable; fixed-size initialized output, no fd ownership transfer.
                let n = unsafe { libc::read(0, bytes.as_mut_ptr().cast(), bytes.len()) };
                if n == 0 {
                    break;
                }
                if n < 0 {
                    let error = io::Error::last_os_error();
                    if matches!(
                        error.kind(),
                        io::ErrorKind::Interrupted | io::ErrorKind::WouldBlock
                    ) {
                        continue;
                    }
                    return Err(error);
                }
                for byte in &bytes[..n as usize] {
                    if let Some(fps) = self.commands.push(*byte)? {
                        self.fps = fps;
                        self.next = None;
                        if self.peer.as_ref().is_some_and(|p| p.started) {
                            self.backend.control(fps)?;
                        }
                    }
                }
            }
        }
        Ok(())
    }
}
/// Fixed phase with no accumulated catch-up work after a missed capture credit.
fn next_deadline(previous: Option<Instant>, now: Instant, period: Duration) -> Instant {
    let previous = previous.unwrap_or(now);
    let steps = (now.saturating_duration_since(previous).as_nanos() / period.as_nanos() + 1)
        .min(u32::MAX as u128) as u32;
    previous + period * steps
}
fn main() {
    let Some(config) = Config::parse(std::env::args_os().skip(1)) else {
        std::process::exit(2)
    };
    let name = if config.hook.is_some() {
        "vkcapture"
    } else {
        "kms"
    };
    let mut source = None;
    let result = (|| {
        signals()?;
        source = Some(Source::new(config)?);
        source.as_mut().expect("initialized source").run()
    })();
    let (frames, dropped) = source
        .as_ref()
        .map_or((0, 0), |source| (source.delivered, source.dropped));
    // Close sessions and sockets before the final lifecycle record.
    drop(source);
    if let Err(error) = &result {
        eprintln!(
            "DPE direct source: {} (errno {})",
            error,
            error.raw_os_error().unwrap_or(0)
        );
    }
    println!(
        "{{\"event\":\"ended\",\"backend\":\"{name}\",\"transport\":\"direct-gpu\",\"frames\":{frames},\"dropped\":{dropped},\"failed\":{},\"errorCode\":\"{}\"}}",
        result.is_err(),
        if result.is_err() {
            "direct-source-failed"
        } else {
            ""
        }
    );
    for stage in 0..32 {
        let mut values = [0; 4];
        // SAFETY: four-element initialized output for the profiling C ABI.
        if unsafe { profiler::dpe_profile_stage(stage, values.as_mut_ptr()) } && values[0] != 0 {
            eprintln!(
                "DPE capture profile {stage} {} {} {} {}",
                values[0], values[1], values[2], values[3]
            );
        }
    }
    if result.is_err() {
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config(args: &[&str]) -> Option<Config> {
        Config::parse(args.iter().map(std::ffi::OsString::from))
    }
    #[test]
    fn bounded_cli_commands_and_fixed_phase_pacing() {
        assert!(config(&["--output", "card0-DP-2", "--fps", "240"]).is_some());
        for args in [
            &["--output", "screen", "--fps", "241"][..],
            &["--output", "screen", "--fps", "+60"],
            &["--hook-fd", "3"],
            &["--output", "screen", "--hook-fd", "3"],
            &["--output", "screen", "--authorize", "false"],
            &["--output"],
        ] {
            assert!(config(args).is_none(), "{args:?}");
        }
        assert!(
            config(&[
                "--hook-fd",
                "3",
                "--device-uuid",
                "00000000000000000000000000000000"
            ])
            .is_some()
        );
        assert!(
            config(&[
                "--hook-fd",
                "3",
                "--device-uuid",
                "gg000000000000000000000000000000"
            ])
            .is_none()
        );
        let mut commands = Commands::default();
        let mut rates = Vec::new();
        // Real main-process worker.send() serializes frame-rate changes as JSONL.
        for byte in b"rate 0\n{\"op\":\"rate\",\"fps\":30}\nrate 60\n" {
            if let Some(rate) = commands.push(*byte).unwrap() {
                rates.push(rate);
            }
        }
        assert_eq!(rates, [0, 30, 60]);
        for bytes in [
            b"rate 241\n".as_slice(),
            b"rate -1\n",
            b"rate 60x\n",
            b"rate 60\0",
            &[b'x'; 64],
        ] {
            let mut commands = Commands::default();
            assert!(bytes.iter().any(|byte| commands.push(*byte).is_err()));
        }
        let now = Instant::now();
        let period = Duration::from_nanos(1_000_000_000 / 240);
        assert_eq!(next_deadline(None, now, period), now + period);
        assert_eq!(
            next_deadline(
                Some(now),
                now + period * 17 + Duration::from_nanos(123),
                period
            ),
            now + period * 18
        );
        assert_eq!(escaped("a\"\\\n"), "\"a\\\"\\\\\\u000a\"");
    }
    fn source(socket: OwnedFd) -> Source {
        Source {
            endpoint: Endpoint::in_runtime(&std::env::temp_dir()).unwrap(),
            backend: Backend::Game(Box::new(game::Game::fixture(system::pair().0))),
            peer: Some(Peer {
                socket,
                started: false,
            }),
            fps: 60,
            held: None,
            next: None,
            changed: None,
            sequence: 0,
            delivered: 0,
            dropped: 0,
            commands: Commands::default(),
        }
    }
    #[test]
    fn selected_disconnect_and_malformed_packet_are_distinct() {
        let (socket, client) = system::pair();
        let mut source = source(socket);
        drop(client);
        assert!(source.client_input().unwrap());
        assert!(source.peer.is_none());
        assert!(source.endpoint.server.is_some());
        let (socket, client) = system::pair();
        source.peer = Some(Peer {
            socket,
            started: false,
        });
        wire::send_raw(client.as_fd(), &[0; direct::SIZE], &[]).unwrap();
        assert!(source.client_input().is_err());
        assert!(source.peer.is_some());
    }
    #[test]
    fn consumer_reset_with_unread_frame_cancels_without_acknowledging_lease() {
        let (socket, client) = system::pair();
        let (producer, hook) = system::pair();
        let mut source = source(socket);
        source.backend = Backend::Game(Box::new(game::Game::fixture(producer)));
        source.peer.as_mut().unwrap().started = true;
        source.held = Some(Lease {
            generation: 7,
            sequence: 9,
        });
        wire::send_raw(
            source.peer.as_ref().unwrap().socket.as_fd(),
            &Packet::new(direct::FRAME).bytes(),
            &[],
        )
        .unwrap();
        drop(client);
        assert!(!source.client_input().unwrap());
        assert_eq!(source.held.unwrap().sequence, 9);
        let mut reply = [0; 128];
        assert!(
            wire::receive(hook.as_fd(), &mut reply, 0)
                .unwrap()
                .is_none()
        );
    }
    #[test]
    fn single_consumer_rates_and_exact_release_tokens() {
        let (socket, client) = system::pair();
        let (producer, hook) = system::pair();
        let mut source = source(socket);
        source.backend = Backend::Game(Box::new(game::Game::fixture(producer)));
        let mut send = |packet: Packet| {
            wire::send(client.as_fd(), &packet.bytes(), &[]).unwrap();
            source.client_input()
        };
        assert!(
            send(Packet {
                fps: 60,
                ..Packet::new(direct::START)
            })
            .unwrap()
        );
        let mut bytes = [0; 128];
        wire::receive(hook.as_fd(), &mut bytes, 0).unwrap().unwrap();
        assert_eq!(dpe_game::protocol::Packet::parse(&bytes).unwrap().fps, 60);
        assert!(send(Packet::new(direct::START)).is_err());
        assert!(
            send(Packet {
                fps: 0,
                ..Packet::new(direct::RATE)
            })
            .unwrap()
        );
        wire::receive(hook.as_fd(), &mut bytes, 0).unwrap().unwrap();
        assert_eq!(dpe_game::protocol::Packet::parse(&bytes).unwrap().flags, 0);
        assert!(source.endpoint.server.is_none());
        source.held = Some(Lease {
            generation: 7,
            sequence: 9,
        });
        wire::send(
            client.as_fd(),
            &Packet {
                generation: 7,
                sequence: 8,
                ..Packet::new(direct::RELEASE)
            }
            .bytes(),
            &[],
        )
        .unwrap();
        assert!(source.client_input().is_err());
        assert!(source.held.is_some());
        wire::send(
            client.as_fd(),
            &Packet {
                generation: 7,
                sequence: 9,
                ..Packet::new(direct::RELEASE)
            }
            .bytes(),
            &[],
        )
        .unwrap();
        assert!(source.client_input().unwrap());
        assert!(source.held.is_none());
        wire::receive(hook.as_fd(), &mut bytes, 0).unwrap().unwrap();
        let released = dpe_game::protocol::Packet::parse(&bytes).unwrap();
        assert_eq!((released.generation, released.sequence), (7, 9));
        drop(client);
        assert!(!source.client_input().unwrap());
    }
}
