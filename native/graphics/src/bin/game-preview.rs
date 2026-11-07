// SPDX-License-Identifier: GPL-3.0-only
//! One explicitly selected same-user hook. Only bounded thumbnails reach stdout.
use dpe_game::protocol as hook;
use dpe_graphics::game_preview::Preview;
use dpe_os::wire;
use std::{
    collections::HashSet,
    ffi::CString,
    io::{self, Read, Write},
    os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
    path::PathBuf,
    ptr,
    time::{Duration, Instant},
};
struct Options {
    fd: i32,
    width: u32,
    height: u32,
    persistent: bool,
    obs: bool,
    uuid: Option<[u8; 16]>,
    node: String,
}
fn invalid() -> io::Error {
    io::ErrorKind::InvalidData.into()
}
impl Options {
    fn parse(args: impl Iterator<Item = String>) -> Option<Self> {
        let mut value = Self {
            fd: -1,
            width: 0,
            height: 0,
            persistent: false,
            obs: false,
            uuid: None,
            node: "auto".into(),
        };
        let mut args = args;
        let mut seen = HashSet::new();
        while let Some(key) = args.next() {
            if !seen.insert(key.clone()) {
                return None;
            }
            let arg = args.next()?;
            match key.as_str() {
                "--hook-fd" => value.fd = arg.parse::<u32>().ok()?.try_into().ok()?,
                "--width" => value.width = arg.parse().ok()?,
                "--height" => value.height = arg.parse().ok()?,
                "--render-node" => value.node = arg,
                "--persistent" if arg == "1" => value.persistent = true,
                "--hook-protocol" if arg == "obs" => value.obs = true,
                "--hook-pause" if arg == "yes" => (), // Demand-paced preview keeps its existing lease gate.
                "--device-uuid" if arg.len() == 32 && arg.is_ascii() => {
                    let mut uuid = [0; 16];
                    for (i, slot) in uuid.iter_mut().enumerate() {
                        *slot = u8::from_str_radix(&arg[i * 2..i * 2 + 2], 16).ok()?;
                    }
                    value.uuid = Some(uuid);
                }
                _ => return None,
            }
        }
        (value.fd >= 0
            && (1..=480).contains(&value.width)
            && (1..=270).contains(&value.height)
            && (value.obs || value.uuid.is_some())
            && (value.node == "auto" || value.node.starts_with("/dev/dri/renderD")))
        .then_some(value)
    }
}
fn socket(options: &Options) -> io::Result<OwnedFd> {
    let mut kind = 0;
    let mut length = size_of_val(&kind) as libc::socklen_t;
    // SAFETY: validates a process-inherited descriptor before adopting it. No
    // other thread exists that could close/reuse it between validation and adopt.
    if unsafe {
        libc::getsockopt(
            options.fd,
            libc::SOL_SOCKET,
            libc::SO_TYPE,
            (&raw mut kind).cast(),
            &mut length,
        )
    } != 0
        || length as usize != size_of_val(&kind)
        || kind
            != if options.obs {
                libc::SOCK_STREAM
            } else {
                libc::SOCK_SEQPACKET
            }
        || !wire::dpe_direct_peer(options.fd)
    {
        return Err(invalid());
    }
    // SAFETY: CLI transfers this validated inherited descriptor exactly once.
    Ok(unsafe { OwnedFd::from_raw_fd(options.fd) })
}
fn wait(fd: BorrowedFd<'_>, deadline: Instant) -> io::Result<bool> {
    loop {
        let Some(left) = deadline.checked_duration_since(Instant::now()) else {
            return Ok(false);
        };
        let mut event = libc::pollfd {
            fd: fd.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        let timeout = libc::timespec {
            tv_sec: left.as_secs() as _,
            tv_nsec: left.subsec_nanos().into(),
        };
        // SAFETY: initialized event and relative timeout, no signal-mask change.
        let result = unsafe { libc::ppoll(&mut event, 1, &timeout, ptr::null()) };
        if result < 0 {
            let e = io::Error::last_os_error();
            if e.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(e);
        }
        return Ok(result > 0
            && event.revents & libc::POLLIN != 0
            && event.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) == 0);
    }
}
fn signaled(fd: BorrowedFd<'_>) -> bool {
    dpe_graphics::game_preview::fence_signaled(fd)
}
fn readable_now(fd: BorrowedFd<'_>) -> io::Result<bool> {
    let mut event = libc::pollfd {
        fd: fd.as_raw_fd(),
        events: libc::POLLIN,
        revents: 0,
    };
    // SAFETY: one initialized live descriptor; nonblocking readiness query.
    let result = unsafe { libc::poll(&mut event, 1, 0) };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    if event.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
        return Err(io::ErrorKind::UnexpectedEof.into());
    }
    Ok(event.revents & libc::POLLIN != 0)
}
fn alarm() {
    // SAFETY: disposable single-threaded helper retains the original bounded
    // watchdog, including blocking stdout/stdin and GPU-driver failures.
    unsafe {
        libc::alarm(4);
    }
}
fn open_preview(options: &Options) -> io::Result<Preview> {
    let nodes = if options.node == "auto" {
        let mut nodes = std::fs::read_dir("/dev/dri")?
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .is_some_and(|s| s.as_encoded_bytes().starts_with(b"renderD"))
            })
            .take(16)
            .collect::<Vec<_>>();
        nodes.sort();
        nodes
    } else {
        vec![PathBuf::from(&options.node)]
    };
    for node in nodes {
        let node = CString::new(node.as_os_str().as_encoded_bytes())?;
        if let Some(preview) = Preview::open(&node)
            && (options.obs || options.uuid == Some(preview.uuid()))
        {
            return Ok(preview);
        }
    }
    Err(io::ErrorKind::Unsupported.into())
}
fn base64(data: &[u8], output: &mut impl Write) -> io::Result<()> {
    const ABC: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut buffer = [0u8; 4096];
    let mut length = 0;
    for chunk in data.chunks(3) {
        let value = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        buffer[length..length + 4].copy_from_slice(&[
            ABC[(value >> 18) as usize],
            ABC[((value >> 12) & 63) as usize],
            if chunk.len() > 1 {
                ABC[((value >> 6) & 63) as usize]
            } else {
                b'='
            },
            if chunk.len() > 2 {
                ABC[(value & 63) as usize]
            } else {
                b'='
            },
        ]);
        length += 4;
        if length == buffer.len() {
            output.write_all(&buffer)?;
            length = 0;
        }
    }
    output.write_all(&buffer[..length])
}
fn run(options: Options, socket: OwnedFd) -> io::Result<()> {
    alarm();
    let mut preview = open_preview(&options)?;
    let control = hook::Packet {
        flags: hook::CAPTURE,
        fps: if options.persistent { 30 } else { 1 },
        uuid: preview.uuid(),
        ..hook::Packet::new(hook::CONTROL)
    };
    if options.obs {
        let mut bytes = [0u8; 32];
        bytes[0] = 1;
        bytes[4..20].copy_from_slice(&control.uuid);
        wire::send_raw(socket.as_fd(), &bytes, &[])?;
    } else {
        wire::send_raw(socket.as_fd(), &control.bytes(), &[])?;
    }
    let mut pixels = vec![0u8; options.width as usize * options.height as usize * 4];
    let mut generation = 0u64;
    let mut sequence = 0u64;
    let mut output = io::BufWriter::new(io::stdout().lock());
    let mut input = io::stdin().lock();
    loop {
        alarm();
        let deadline = Instant::now() + Duration::from_millis(2500);
        let mut bytes = [0u8; 128];
        let mut buffered = 0usize;
        let mut descriptors: [Option<OwnedFd>; 4] = std::array::from_fn(|_| None);
        let mut count = 0usize;
        let mut ready = options.obs && generation != 0 && !readable_now(socket.as_fd())?;
        for _ in 0..32 {
            if ready {
                break;
            }
            if !wait(socket.as_fd(), deadline)? {
                return Err(io::ErrorKind::TimedOut.into());
            }
            let Some((length, message)) =
                wire::receive_chunk(socket.as_fd(), &mut bytes[buffered..], 4 - count)?
            else {
                continue;
            };
            buffered += length;
            for owner in message.descriptors.into_iter().flatten() {
                descriptors[count] = Some(owner);
                count += 1;
            }
            if buffered != bytes.len() {
                if !options.obs {
                    return Err(invalid());
                }
                continue;
            }
            buffered = 0;
            let packet = if options.obs {
                hook::Packet::parse_obs(
                    &bytes,
                    count,
                    generation.checked_add(1).ok_or_else(invalid)?,
                )
            } else {
                hook::Packet::parse(&bytes)
            }
            .ok_or_else(invalid)?;
            if packet.kind == hook::TEXTURE
                && packet.nfd as usize == count
                && count > 0
                && packet.generation != 0
            {
                let mut fds = [descriptors[0]
                    .as_ref()
                    .expect("adopted first descriptor")
                    .as_fd(); 4];
                for (slot, owner) in fds.iter_mut().zip(&descriptors[..count]) {
                    *slot = owner.as_ref().expect("adopted descriptor").as_fd();
                }
                if !preview.import(&packet, &fds[..count]) {
                    return Err(invalid());
                }
                generation = packet.generation;
                if options.obs {
                    // OBS has no frame notification; preserve its bounded first
                    // present head start. A timeout adds no source ownership.
                    std::thread::sleep(Duration::from_millis(25));
                    ready = true;
                }
            } else if packet.kind == hook::FRAME
                && generation != 0
                && packet.generation == generation
                && packet.sequence > sequence
            {
                ready = (packet.flags == 0 && packet.nfd == 0 && count == 0)
                    || (packet.flags == hook::SYNC_FD
                        && packet.nfd == 1
                        && count == 1
                        && wait(
                            descriptors[0].as_ref().expect("frame fence").as_fd(),
                            deadline,
                        )?
                        && signaled(descriptors[0].as_ref().expect("frame fence").as_fd()));
                if !ready {
                    return Err(invalid());
                }
                sequence = packet.sequence;
            } else {
                return Err(invalid());
            }
            descriptors.fill_with(|| None);
            count = 0;
        }
        if !ready {
            return Err(io::ErrorKind::TimedOut.into());
        }
        let (width, height) = preview
            .render((options.width, options.height), &mut pixels)
            .ok_or_else(invalid)?;
        if !options.obs {
            // GPU readback has completed before releasing this exact hook lease.
            let release = hook::Packet {
                generation,
                sequence,
                ..hook::Packet::new(hook::RELEASE)
            };
            wire::send_raw(socket.as_fd(), &release.bytes(), &[])?;
        }
        write!(
            output,
            "{{\"width\":{width},\"height\":{height},\"format\":\"BGRA\",\"pixels\":\""
        )?;
        base64(&pixels[..width as usize * height as usize * 4], &mut output)?;
        output.write_all(b"\"}\n")?;
        output.flush()?;
        if !options.persistent {
            return Ok(());
        }
        let mut demand = [0u8; 1];
        if input.read(&mut demand)? != 1 || demand[0] != b'\n' {
            return Ok(());
        }
    }
}
fn main() {
    let Some(options) = Options::parse(std::env::args().skip(1)) else {
        std::process::exit(2);
    };
    let socket = match socket(&options) {
        Ok(fd) => fd,
        Err(_) => std::process::exit(2),
    };
    if let Err(error) = run(options, socket) {
        eprintln!("DPE game preview: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bounded_base64_handles_tails_and_chunk_boundaries() {
        for (input, expected) in [
            ("", ""),
            ("f", "Zg=="),
            ("fo", "Zm8="),
            ("foo", "Zm9v"),
            ("foob", "Zm9vYg=="),
            ("foobar", "Zm9vYmFy"),
        ] {
            let mut result = Vec::new();
            base64(input.as_bytes(), &mut result).unwrap();
            assert_eq!(result, expected.as_bytes());
        }
        let mut result = Vec::new();
        base64(&vec![0; 3073], &mut result).unwrap();
        assert_eq!(result.len(), 4100);
        assert!(result[..4098].iter().all(|&b| b == b'A'));
        assert_eq!(&result[4098..], b"==");
    }
    #[test]
    fn options_reject_duplicates_and_unbounded_thumbnails_before_opening_devices() {
        let args = [
            "--hook-fd",
            "3",
            "--width",
            "320",
            "--height",
            "180",
            "--device-uuid",
            "00112233445566778899aabbccddeeff",
        ];
        let options = Options::parse(args.iter().map(|s| s.to_string())).unwrap();
        assert_eq!((options.width, options.height), (320, 180));
        for extra in [
            ["--width", "0"],
            ["--height", "271"],
            ["--persistent", "0"],
            ["--hook-protocol", "dpe"],
        ] {
            assert!(Options::parse(args.into_iter().chain(extra).map(str::to_owned)).is_none());
        }
    }
}
