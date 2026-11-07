// SPDX-License-Identifier: GPL-3.0-only
//! Bounded monitor thumbnail. Only the requested thumbnail is read to RAM;
//! no PipeWire source, encoder, portal or pixel file is created.
use dpe_core::geometry::Rect;
use dpe_graphics::{
    dmabuf::{self, Allocation, Allocator},
    kms_capture::Renderer,
};
use dpe_kms::capture::{self, Capture, Image, Plane};
use dpe_wayland::capture::{Capture as Wayland, Event, Plane as WaylandPlane};
use std::{
    collections::HashMap,
    ffi::CString,
    io::{Read, Write},
    os::fd::AsRawFd,
    time::{Duration, Instant},
};
struct Options {
    method: String,
    output: CString,
    width: u32,
    height: u32,
    persistent: bool,
}
impl Options {
    fn parse(args: impl Iterator<Item = String>) -> Option<Self> {
        let mut args = args;
        let mut values = HashMap::new();
        while let Some(key) = args.next() {
            if values.insert(key, args.next()?).is_some() {
                return None;
            }
        }
        let method = values.remove("--method")?;
        let output = CString::new(values.remove("--output")?).ok()?;
        let width = values.remove("--width")?.parse().ok()?;
        let height = values.remove("--height")?.parse().ok()?;
        let persistent = match values.remove("--persistent") {
            Some(value) if value == "1" => true,
            None => false,
            _ => return None,
        };
        (values.is_empty()
            && matches!(method.as_str(), "kms" | "wayland-direct")
            && !output.is_empty()
            && output.as_bytes().len() <= 128
            && (16..=480).contains(&width)
            && (16..=270).contains(&height))
        .then_some(Self {
            method,
            output,
            width,
            height,
            persistent,
        })
    }
}
fn demand(options: &Options) -> bool {
    if !options.persistent {
        return false;
    }
    // SAFETY: this dedicated helper installs a bounded watchdog; blocking input
    // and compositor/GPU startup cannot leave an orphaned preview process.
    unsafe {
        libc::alarm(3);
    }
    let mut byte = [0];
    let result = std::io::stdin().read_exact(&mut byte).is_ok() && byte[0] == b'\n';
    // SAFETY: restart the same process-local watchdog for the next thumbnail.
    unsafe {
        libc::alarm(3);
    }
    result
}
fn emit(
    renderer: &Renderer,
    primary: Image<'_>,
    cursor: Option<Image<'_>>,
    canvas: (u32, u32),
    options: &Options,
    pixels: &mut [u8],
) -> Option<()> {
    let (width, height) = renderer.thumbnail(
        &primary,
        cursor.as_ref(),
        canvas,
        (options.width, options.height),
        pixels,
    )?;
    let mut output = std::io::stdout().lock();
    writeln!(output, "DPE-PREVIEW {width} {height}").ok()?;
    output
        .write_all(pixels.get(..width as usize * height as usize * 4)?)
        .ok()?;
    output.flush().ok()
}
fn kms(options: &Options) -> Option<()> {
    // Thumbnails never open a new authentication prompt.
    if capture::helper_status().is_some() || capture::authorization_required() {
        return None;
    }
    let mut capture = Capture::open(&options.output, c"auto").ok()?;
    let size = capture.dimensions();
    let renderer = Renderer::open(capture.render_path())?;
    let mut pixels = vec![0; 480 * 270 * 4];
    loop {
        let mut rendered = false;
        for _ in 0..3 {
            let frame = capture.next(|| false).ok()?;
            if let Some(frame) = frame {
                emit(
                    &renderer,
                    frame.primary(),
                    frame.cursor(),
                    size,
                    options,
                    &mut pixels,
                )?;
                rendered = true;
                break;
            }
        }
        if !rendered {
            return None;
        }
        if !demand(options) {
            return Some(());
        }
    }
}
fn wayland(options: &Options) -> Option<()> {
    let mut capture =
        Wayland::<std::rc::Rc<Allocation>>::connect(|format, dma| dma && dmabuf::rgba(format))
            .ok()?;
    capture.start(options.output.to_str().ok()?, true).ok()?;
    let mut renderer = None;
    let mut pixels = vec![0; 480 * 270 * 4];
    let mut submitted = false;
    let mut deadline = Instant::now() + Duration::from_secs(3);
    loop {
        capture.dispatch().ok()?;
        while let Some(event) = capture.next_event() {
            match event {
                Event::Constraints(constraints) => {
                    if submitted {
                        return None;
                    }
                    let node = dmabuf::render_node(constraints.device?)?;
                    let allocator = Allocator::open(&node)?;
                    renderer = Some(Renderer::open(&node)?);
                    let allocation = constraints
                        .formats
                        .iter()
                        .filter(|format| format.dmabuf && dmabuf::rgba(format.format))
                        .find_map(|format| {
                            allocator
                                .create(
                                    constraints.width,
                                    constraints.height,
                                    format.format,
                                    format.modifier,
                                )
                                .map(|storage| (*format, storage))
                        })?;
                    let (format, storage) = allocation;
                    let storage = std::rc::Rc::new(storage);
                    let planes: Vec<_> = storage
                        .planes()
                        .iter()
                        .map(|plane| WaylandPlane {
                            descriptor: plane.descriptor(),
                            stride: plane.stride,
                            offset: plane.offset,
                        })
                        .collect();
                    let buffer = capture
                        .allocate(&constraints, format, &planes, storage.clone())
                        .ok()?;
                    capture.submit(buffer).ok()?;
                    submitted = true;
                }
                Event::Frame {
                    buffer,
                    frame,
                    status,
                } => {
                    if status != 0 || frame.transform != 0 {
                        return None;
                    }
                    let storage = buffer.storage();
                    let rect = Rect {
                        x: 0,
                        y: 0,
                        width: storage.width,
                        height: storage.height,
                    };
                    let mut planes = [None; 4];
                    for (target, plane) in planes.iter_mut().zip(storage.planes()) {
                        *target = Some(Plane {
                            descriptor: plane.descriptor(),
                            stride: plane.stride,
                            offset: plane.offset,
                        });
                    }
                    let image = Image {
                        width: storage.width,
                        height: storage.height,
                        format: storage.format,
                        modifier: storage.modifier,
                        source: rect,
                        destination: rect,
                        planes,
                    };
                    emit(
                        renderer.as_ref()?,
                        image,
                        None,
                        (storage.width, storage.height),
                        options,
                        &mut pixels,
                    )?;
                    if !demand(options) {
                        return Some(());
                    }
                    deadline = Instant::now() + Duration::from_secs(3);
                    capture.submit(buffer).ok()?;
                }
                Event::Stopped(_) => return None,
            }
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return None;
        }
        let mut fd = libc::pollfd {
            fd: capture.descriptor().as_raw_fd(),
            events: libc::POLLIN
                | if capture.needs_flush() {
                    libc::POLLOUT
                } else {
                    0
                },
            revents: 0,
        };
        // SAFETY: one initialized record borrows this dedicated connection.
        if unsafe { libc::poll(&mut fd, 1, remaining.as_millis().min(3000) as i32) } < 0
            && std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted
        {
            return None;
        }
    }
}
fn main() {
    let Some(options) = Options::parse(std::env::args().skip(1)) else {
        std::process::exit(2);
    };
    // SAFETY: this helper owns its process lifecycle; verify its parent did not
    // exit between reading its identity and installing the death signal.
    let okay = unsafe {
        let parent = libc::getppid();
        libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) == 0 && libc::getppid() == parent
    };
    if !okay {
        std::process::exit(1);
    }
    // SAFETY: process-local watchdog, before opening any capture connection.
    unsafe {
        libc::alarm(3);
    }
    let okay = if options.method == "kms" {
        kms(&options)
    } else {
        wayland(&options)
    };
    if okay.is_none() {
        std::process::exit(1);
    }
}
