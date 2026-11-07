// SPDX-License-Identifier: GPL-3.0-only
//! Safe native producer interface. A returned frame owns every export and can
//! outlive its capture session; no C cleanup contract or raw token is required.
use crate::{drm, ffi, metadata, scene, startup, transport};
use dpe_core::geometry::Rect;
use dpe_os::profiler::Span;
use std::{
    ffi::CStr,
    fs::OpenOptions,
    os::{
        fd::{AsRawFd, BorrowedFd},
        unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt},
    },
    time::{Duration, Instant},
};
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    Unavailable,
    Permission,
    Transport,
    Unsupported,
    Cancelled,
    Session(&'static CStr),
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "KMS capture {self:?}")
    }
}
impl std::error::Error for Error {}
pub struct Output {
    pub id: String,
    pub name: String,
    pub width: u32,
    pub height: u32,
    pub supported: bool,
}
pub fn helper_status() -> Option<&'static CStr> {
    startup::helper_status()
}
pub fn authorization_required() -> bool {
    startup::dpe_kms_authorization_required()
}
pub fn outputs() -> Vec<Output> {
    let mut outputs = [ffi::dpe_kms_output::default(); 32];
    // SAFETY: inventory receives exactly the declared 32 initialized records.
    let count = unsafe { metadata::dpe_kms_inventory(outputs.as_mut_ptr()) } as usize;
    outputs
        .get(..count)
        .unwrap_or_default()
        .iter()
        .map(|output| Output {
            id: String::from_utf8_lossy(&bytes(&output.id)).into_owned(),
            name: String::from_utf8_lossy(&bytes(&output.name)).into_owned(),
            width: output.width,
            height: output.height,
            supported: output.supported,
        })
        .collect()
}
#[derive(Clone, Copy)]
pub struct Plane<'a> {
    pub descriptor: BorrowedFd<'a>,
    pub offset: u32,
    pub stride: u32,
}
pub struct Image<'a> {
    pub width: u32,
    pub height: u32,
    pub format: u32,
    pub modifier: u64,
    pub source: Rect,
    pub destination: Rect,
    pub planes: [Option<Plane<'a>>; 4],
}
pub struct Frame {
    reply: transport::Reply,
    primary: usize,
    cursor: Option<usize>,
}
impl Frame {
    fn image(&self, index: usize) -> Option<Image<'_>> {
        let response = self.reply.metadata();
        let item = response.items.get(index)?;
        let mut start = 0;
        for previous in &response.items[..index] {
            start += previous.num_dma_bufs as usize;
        }
        let mut planes = [None; 4];
        for (i, plane) in planes
            .iter_mut()
            .enumerate()
            .take(item.num_dma_bufs as usize)
        {
            *plane = Some(Plane {
                descriptor: self.reply.descriptor(start + i)?,
                offset: item.dma_buf[i].offset,
                stride: item.dma_buf[i].pitch,
            });
        }
        Some(Image {
            width: item.width,
            height: item.height,
            format: item.pixel_format,
            modifier: item.modifier,
            source: Rect {
                x: item.src_x,
                y: item.src_y,
                width: item.src_w.try_into().ok()?,
                height: item.src_h.try_into().ok()?,
            },
            destination: Rect {
                x: item.dst_x,
                y: item.dst_y,
                width: item.dst_w.try_into().ok()?,
                height: item.dst_h.try_into().ok()?,
            },
            planes,
        })
    }
    pub fn primary(&self) -> Image<'_> {
        self.image(self.primary).expect("validated primary image")
    }
    pub fn cursor(&self) -> Option<Image<'_>> {
        self.cursor.and_then(|index| self.image(index))
    }
}
pub struct Capture {
    session: startup::Session,
    inspector: metadata::Inspector,
    output: ffi::dpe_kms_output,
    device: (u32, u32),
}
/// Owning the pending state makes a second in-flight request impossible. Drop
/// disconnects the session before storage can be reused after timeout/cancel.
pub struct Pending {
    capture: Capture,
    deadline: Instant,
}
#[expect(
    clippy::large_enum_variant,
    reason = "Keep the fixed KMS reply inline instead of allocating a box for every captured frame"
)]
pub enum Response {
    Waiting(Pending),
    Ready {
        capture: Capture,
        frame: Option<Frame>,
    },
}
impl Pending {
    pub fn descriptor(&self) -> BorrowedFd<'_> {
        self.capture.session.socket().expect("live capture session")
    }
    pub fn remaining(&self) -> Duration {
        self.deadline.saturating_duration_since(Instant::now())
    }
    /// Nonblocking completion for the PipeWire producer actor. A transient DRM
    /// layout change completes the request with no frame, preserving the same
    /// session for a later paced attempt. Errors retire the complete owner.
    pub fn receive(self) -> Result<Response, Error> {
        if self.remaining().is_zero() {
            return Err(Error::Transport);
        }
        let _profile = Span::new(15);
        let reply = match transport::Reply::receive(self.descriptor()) {
            Ok(Some(reply)) => reply,
            Ok(None) => return Ok(Response::Waiting(self)),
            Err(transport::Error::Permission) => return Err(Error::Permission),
            Err(transport::Error::Invalid) => return Err(Error::Transport),
        };
        drop(_profile);
        let frame = self.capture.decode(reply)?;
        Ok(Response::Ready {
            capture: self.capture,
            frame,
        })
    }
}
fn bytes(value: &[libc::c_char]) -> Vec<u8> {
    value
        .iter()
        .map(|value| *value as u8)
        .take_while(|value| *value != 0)
        .collect()
}
impl Capture {
    pub fn open(selected: &CStr, render: &CStr) -> Result<Self, Error> {
        let mut outputs = [ffi::dpe_kms_output::default(); 32];
        // SAFETY: inventory writes at most the declared 32 fully initialized records.
        let count = unsafe { metadata::dpe_kms_inventory(outputs.as_mut_ptr()) } as usize;
        let output = outputs
            .get(..count)
            .ok_or(Error::Unsupported)?
            .iter()
            .find(|output| output.supported && bytes(&output.id) == selected.to_bytes())
            .copied()
            .ok_or(Error::Unavailable)?;
        let render_path = bytes(&output.render);
        if render != c"auto" && render.to_bytes() != render_path {
            return Err(Error::Unavailable);
        };
        use std::os::unix::ffi::OsStrExt;
        let path = std::path::Path::new(std::ffi::OsStr::from_bytes(&render_path));
        let device = std::fs::symlink_metadata(path).map_err(|_| Error::Unavailable)?;
        if !device.file_type().is_char_device() {
            return Err(Error::Unsupported);
        };
        let device = (libc::major(device.rdev()), libc::minor(device.rdev()));
        let card = bytes(&output.card);
        let card = CStr::from_bytes_with_nul(&[card, vec![0]].concat())
            .map_err(|_| Error::Unsupported)?
            .to_owned();
        let metadata = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(std::path::Path::new(std::ffi::OsStr::from_bytes(
                card.to_bytes(),
            )))
            .map_err(|_| Error::Unavailable)?;
        let metadata = drm::Node::new(metadata.into()).ok_or(Error::Unsupported)?;
        if !metadata.atomic_metadata() {
            return Err(Error::Unsupported);
        };
        let inspector = metadata::Inspector::new(metadata, output).ok_or(Error::Unsupported)?;
        let session = startup::open(&card, output.connector).map_err(Error::Session)?;
        Ok(Self {
            session,
            inspector,
            output,
            device,
        })
    }
    pub fn dimensions(&self) -> (u32, u32) {
        (self.output.width, self.output.height)
    }
    pub fn device(&self) -> (u32, u32) {
        self.device
    }
    pub fn render_path(&self) -> &CStr {
        let bytes = &self.output.render;
        // The inventory always initializes/terminates this fixed array and
        // Capture::open already verified the same bytes as a live render node.
        let length = bytes
            .iter()
            .position(|byte| *byte == 0)
            .expect("terminated render path");
        // SAFETY: c_char and u8 share size/alignment; the bounded initialized
        // record is immutably borrowed through this returned terminated view.
        let bytes = unsafe { std::slice::from_raw_parts(bytes.as_ptr().cast(), length + 1) };
        CStr::from_bytes_with_nul(bytes).expect("validated render path")
    }
    pub fn request(self) -> Result<Pending, Error> {
        let socket = self.session.socket().ok_or(Error::Transport)?;
        if !transport::request(socket, self.session.connector()) {
            return Err(Error::Transport);
        }
        Ok(Pending {
            capture: self,
            deadline: Instant::now() + Duration::from_millis(500),
        })
    }
    /// One request owns the socket until its complete reply. Interrupted waits
    /// retain the same request, with a monotonic deadline and bounded cancellation.
    /// `None` is a transient layout change. Keeping this fixed protocol record
    /// inline avoids allocating a box for every captured frame.
    pub fn next(&mut self, cancelled: impl Fn() -> bool) -> Result<Option<Frame>, Error> {
        let socket = self.session.socket().ok_or(Error::Transport)?;
        let reply = {
            let _profile = Span::new(15);
            if cancelled() {
                return Err(Error::Cancelled);
            };
            // The session's negotiated connector is zero for an older GSR
            // helper. Preserve its ordinary GET_KMS request in that case.
            if !transport::request(socket, self.session.connector()) {
                return Err(Error::Transport);
            }
            let deadline = Instant::now() + Duration::from_millis(500);
            loop {
                if cancelled() {
                    return Err(Error::Cancelled);
                };
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return Err(Error::Transport);
                };
                let mut wait = libc::pollfd {
                    fd: socket.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                };
                let timeout = remaining.as_millis().clamp(1, 50) as i32;
                // SAFETY: one initialized poll record; the session pins its socket.
                let status = unsafe { libc::poll(&raw mut wait, 1, timeout) };
                if status < 0 {
                    if std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
                        continue;
                    };
                    return Err(Error::Transport);
                }
                if wait.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
                    return Err(Error::Transport);
                };
                if wait.revents & libc::POLLIN != 0 {
                    match transport::Reply::receive(socket) {
                        Ok(Some(reply)) => break reply,
                        Ok(None) => continue,
                        Err(transport::Error::Permission) => return Err(Error::Permission),
                        Err(transport::Error::Invalid) => return Err(Error::Transport),
                    }
                }
            }
        };
        self.decode(reply)
    }
    fn decode(&self, reply: transport::Reply) -> Result<Option<Frame>, Error> {
        let _profile = Span::new(16);
        let layout = self.inspector.metadata().ok_or(Error::Unsupported)?;
        match scene::scene(reply.metadata(), &self.output, &layout) {
            scene::Scene::Changed => Ok(None),
            scene::Scene::Unsupported => Err(Error::Unsupported),
            scene::Scene::Valid { primary, cursor } => {
                let frame = Frame {
                    reply,
                    primary,
                    cursor,
                };
                if frame.image(primary).is_none()
                    || cursor.is_some_and(|index| frame.image(index).is_none())
                {
                    return Err(Error::Unsupported);
                };
                Ok(Some(frame))
            }
        }
    }
}
