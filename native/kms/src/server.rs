// SPDX-License-Identifier: GPL-3.0-only
//! Privileged service boundary for GSR protocol 6. No command execution, pixel
//! mapping, arbitrary file reads, storage, rendering or encoding is exposed.
use crate::{
    drm,
    export::{Exporter, Packet},
    ffi,
};
use dpe_os::wire;
use std::{
    ffi::{CStr, CString},
    fs::OpenOptions,
    mem::MaybeUninit,
    os::{
        fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
        unix::{
            ffi::OsStrExt,
            fs::{FileTypeExt, MetadataExt, OpenOptionsExt},
            net::UnixStream,
        },
    },
    path::Path,
    time::Duration,
};
fn decimal(bytes: &[u8]) -> Option<u32> {
    if bytes.is_empty() || bytes.len() > 10 || !bytes.iter().all(u8::is_ascii_digit) {
        return None;
    }
    bytes
        .iter()
        .try_fold(0u32, |value, byte| {
            value.checked_mul(10)?.checked_add(u32::from(byte - b'0'))
        })
        .filter(|value| *value != 0)
}
fn marker(uid: u32) -> Option<CString> {
    let path = CString::new(format!(
        "/usr/local/libexec/discord-patched-electron/permissions/{uid}"
    ))
    .ok()?;
    if !crate::startup::ancestors(&path) {
        return None;
    }
    crate::permission::Grant::read(Path::new(std::ffi::OsStr::from_bytes(path.to_bytes())))?;
    Some(path)
}
fn socket_path(path: &Path, uid: u32) -> bool {
    let bytes = path.as_os_str().as_bytes();
    let prefix = b"/tmp/dpe-kms-";
    if bytes.len() != prefix.len() + 6 + 7
        || !bytes.starts_with(prefix)
        || !bytes.ends_with(b"/socket")
        || !bytes[prefix.len()..prefix.len() + 6]
            .iter()
            .all(u8::is_ascii_alphanumeric)
    {
        return false;
    }
    let directory = std::ffi::OsStr::from_bytes(&bytes[..prefix.len() + 6]);
    let Ok(parent) = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_PATH | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(directory)
    else {
        return false;
    };
    let Ok(metadata) = parent.metadata() else {
        return false;
    };
    if !metadata.is_dir() || metadata.uid() != uid || metadata.mode() & 0o7777 != 0o700 {
        return false;
    }
    let mut info = MaybeUninit::<libc::stat>::uninit();
    // SAFETY: parent pins the checked directory. fstatat does not follow a
    // substituted socket symlink and writes exactly one stat record.
    if unsafe {
        libc::fstatat(
            parent.as_raw_fd(),
            c"socket".as_ptr(),
            info.as_mut_ptr(),
            libc::AT_SYMLINK_NOFOLLOW,
        )
    } != 0
    {
        return false;
    }
    // SAFETY: successful fstatat initialized the output record.
    let info = unsafe { info.assume_init() };
    info.st_mode & libc::S_IFMT == libc::S_IFSOCK && info.st_uid == uid
}
fn card_path(path: &Path) -> bool {
    let bytes = path.as_os_str().as_bytes();
    let Some(number) = bytes.strip_prefix(b"/dev/dri/card") else {
        return false;
    };
    !number.is_empty() && number.len() <= 10 && number.iter().all(u8::is_ascii_digit)
}
fn open_card(path: &Path) -> Option<drm::Node> {
    if !card_path(path) {
        return None;
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .ok()?;
    let metadata = file.metadata().ok()?;
    if !metadata.file_type().is_char_device() || metadata.uid() != 0 {
        return None;
    }
    // SAFETY: query the live descriptor without adopting or mutating it.
    if unsafe { ffi::drmGetNodeTypeFromFd(file.as_raw_fd()) } != ffi::DRM_NODE_PRIMARY as i32 {
        return None;
    }
    let node = drm::Node::new(file.into())?;
    node.atomic_metadata().then_some(node)
}
/// The caller must choose the exact initialized Linux output type for `name`.
unsafe fn option<T: Copy>(fd: BorrowedFd<'_>, name: i32) -> Option<T> {
    let mut value = MaybeUninit::<T>::uninit();
    let mut size = size_of::<T>() as libc::socklen_t;
    // SAFETY: all callers use the exact Linux output type for this fixed socket
    // option. The socket remains borrowed and output bounds are explicit.
    if unsafe {
        libc::getsockopt(
            fd.as_raw_fd(),
            libc::SOL_SOCKET,
            name,
            value.as_mut_ptr().cast(),
            &mut size,
        )
    } != 0
        || size as usize != size_of::<T>()
    {
        return None;
    }
    // SAFETY: fixed option's successful kernel reply initialized a complete T.
    Some(unsafe { value.assume_init() })
}
fn peer(fd: BorrowedFd<'_>, uid: u32, pid: libc::pid_t, kind: i32) -> bool {
    // SAFETY: Linux documents ucred for SO_PEERCRED and int for both scalar
    // options. No arbitrary type can reach the generic output helper.
    unsafe {
        option::<libc::ucred>(fd, libc::SO_PEERCRED).is_some_and(|p| p.uid == uid && p.pid == pid)
            && option::<i32>(fd, libc::SO_DOMAIN) == Some(libc::AF_UNIX)
            && option::<i32>(fd, libc::SO_TYPE) == Some(kind)
    }
}
enum Request {
    Replace(OwnedFd),
    Capture(Option<u32>),
}
fn request(fd: BorrowedFd<'_>, initial: bool, uid: u32, pid: libc::pid_t) -> Result<Request, ()> {
    let mut bytes = [0u8; 12];
    let Some(mut received) = wire::receive(fd, &mut bytes, 32).map_err(|_| ())? else {
        return Err(());
    };
    let version = u32::from_ne_bytes(bytes[..4].try_into().map_err(|_| ())?);
    let kind = i32::from_ne_bytes(bytes[4..8].try_into().map_err(|_| ())?);
    let valid_kind = if initial {
        kind == 0
    } else {
        kind == 1 || kind == ffi::DPE_KMS_SCOPE_REQUEST as i32
    };
    if version != 6 || !valid_kind || received.count != usize::from(initial) {
        return Err(());
    }
    if !initial {
        if kind == 1 {
            return Ok(Request::Capture(None)); // Standard GSR ignores the final word.
        }
        let connector = u32::from_ne_bytes(bytes[8..12].try_into().map_err(|_| ())?);
        return if connector == 0 || connector == u32::MAX {
            Err(())
        } else {
            Ok(Request::Capture(Some(connector)))
        };
    }
    let replacement = received.descriptors[0].take().ok_or(())?;
    if !peer(replacement.as_fd(), uid, pid, libc::SOCK_SEQPACKET) {
        return Err(());
    }
    Ok(Request::Replace(replacement))
}
fn watcher(path: &CStr) -> Option<OwnedFd> {
    // SAFETY: inotify_init1 creates one new close-on-exec nonblocking descriptor.
    let raw = unsafe { libc::inotify_init1(libc::IN_CLOEXEC | libc::IN_NONBLOCK) };
    if raw < 0 {
        return None;
    }
    // SAFETY: successful syscall returned uniquely owned descriptor.
    let fd = unsafe { OwnedFd::from_raw_fd(raw) };
    // SAFETY: static watch flags and a live C string; no symlink is followed.
    if unsafe {
        libc::inotify_add_watch(
            fd.as_raw_fd(),
            path.as_ptr(),
            libc::IN_DELETE_SELF | libc::IN_MOVE_SELF | libc::IN_ATTRIB | libc::IN_DONT_FOLLOW,
        )
    } < 0
    {
        return None;
    }
    Some(fd)
}
fn poll(
    socket: BorrowedFd<'_>,
    watch: BorrowedFd<'_>,
    authorization: BorrowedFd<'_>,
    timeout: i32,
) -> bool {
    let mut descriptors = [
        libc::pollfd {
            fd: socket.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        },
        libc::pollfd {
            fd: watch.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        },
        libc::pollfd {
            fd: authorization.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        },
    ];
    loop {
        // SAFETY: initialized three-record output and retained borrowed fds.
        let result = unsafe {
            libc::poll(
                descriptors.as_mut_ptr(),
                descriptors.len() as libc::nfds_t,
                timeout,
            )
        };
        if result < 0 && std::io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
            continue;
        }
        // Revocation wins over a simultaneously ready frame request.
        return result > 0
            && descriptors[1].revents == 0
            && descriptors[2].revents == 0
            && descriptors[0].revents & libc::POLLIN != 0;
    }
}
fn serve(
    uid: u32,
    parent: libc::pid_t,
    socket: &Path,
    card: &Path,
    endpoint: &Path,
    token: &str,
) -> Result<(), ()> {
    let permission = marker(uid).ok_or(())?;
    let permission_path = Path::new(std::ffi::OsStr::from_bytes(permission.to_bytes()));
    let grant = crate::permission::Grant::read(permission_path).ok_or(())?;
    if !socket_path(socket, uid) {
        return Err(());
    }
    let watcher = watcher(&permission).ok_or(())?;
    if crate::permission::Grant::read(permission_path).as_ref() != Some(&grant) {
        return Err(());
    }
    let authorization =
        crate::permission::authorize(&grant, uid, parent, endpoint, token).ok_or(())?;
    let node = open_card(card).ok_or(())?;
    let stream = UnixStream::connect(socket).map_err(|_| ())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| ())?;
    if !peer(stream.as_fd(), uid, parent, libc::SOCK_STREAM)
        || !poll(stream.as_fd(), watcher.as_fd(), authorization.as_fd(), 2000)
    {
        return Err(());
    }
    let Request::Replace(socket) = request(stream.as_fd(), true, uid, parent)? else {
        return Err(());
    };
    drop(stream);
    if !Packet::capabilities().send(socket.as_fd()) {
        return Err(());
    }
    let mut exporter = Exporter::new(node);
    while poll(socket.as_fd(), watcher.as_fd(), authorization.as_fd(), -1) {
        let Ok(Request::Capture(connector)) = request(socket.as_fd(), false, uid, parent) else {
            break;
        };
        if !exporter.snapshot(connector).send(socket.as_fd()) {
            break;
        }
    }
    Ok(())
}
/// CLI entry point. Only pkexec with a non-root origin and the fixed installed
/// grant may enter the service. Failure returns before any DRM pixel export.
pub fn main() -> i32 {
    // SAFETY: identity queries have no pointer inputs or side effects.
    if unsafe { libc::geteuid() } != 0 {
        return 1;
    }
    let Some(identity) = std::env::var_os("PKEXEC_UID") else {
        return 1;
    };
    let Some(uid) = decimal(identity.as_bytes()) else {
        return 1;
    };
    let args: Vec<_> = std::env::args_os().collect();
    if args.len() != 5 {
        return 1;
    }
    // SAFETY: getppid reads the real producer parent after pkexec's exec.
    let parent = unsafe { libc::getppid() };
    if parent <= 1 {
        return 1;
    }
    if serve(
        uid,
        parent,
        Path::new(&args[1]),
        Path::new(&args[2]),
        Path::new(&args[3]),
        &args[4].to_string_lossy(),
    )
    .is_ok()
    {
        0
    } else {
        1
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::{
        fs::PermissionsExt,
        net::{UnixDatagram, UnixListener},
    };
    #[test]
    fn revocation_stops_a_simultaneously_ready_frame_request() {
        let _exclusive = crate::FD_TESTS.lock().unwrap();
        let mut template = b"/tmp/dpe-kms-XXXXXX\0".to_vec();
        // SAFETY: writable, NUL-terminated template with six required X bytes.
        assert!(!unsafe { libc::mkdtemp(template.as_mut_ptr().cast()) }.is_null());
        let directory = CString::from_vec_with_nul(template).unwrap();
        let directory = Path::new(std::ffi::OsStr::from_bytes(directory.to_bytes()));
        let grant = directory.join("grant");
        std::fs::write(&grant, b"DPE-KMS-2\n").unwrap();
        let name = CString::new(grant.as_os_str().as_bytes()).unwrap();
        let watch = watcher(&name).unwrap();
        let (sender, receiver) = UnixDatagram::pair().unwrap();
        let (authorization, _owner) = UnixStream::pair().unwrap();
        sender.send(&[1]).unwrap();
        assert!(poll(
            receiver.as_fd(),
            watch.as_fd(),
            authorization.as_fd(),
            100
        ));
        std::fs::remove_file(&grant).unwrap();
        assert!(!poll(
            receiver.as_fd(),
            watch.as_fd(),
            authorization.as_fd(),
            100
        ));
        drop(watch);
        std::fs::remove_dir(directory).unwrap();
    }
    #[test]
    fn discord_exit_wins_over_a_ready_frame() {
        let _exclusive = crate::FD_TESTS.lock().unwrap();
        let (sender, receiver) = UnixStream::pair().unwrap();
        let (authorization, owner) = UnixStream::pair().unwrap();
        // An idle socket is sufficient to model an unchanged grant watch here.
        let (watch, _watch_owner) = UnixStream::pair().unwrap();
        use std::io::Write;
        (&sender).write_all(&[1]).unwrap();
        assert!(poll(
            receiver.as_fd(),
            watch.as_fd(),
            authorization.as_fd(),
            100
        ));
        drop(owner);
        assert!(!poll(
            receiver.as_fd(),
            watch.as_fd(),
            authorization.as_fd(),
            100
        ));
    }
    #[test]
    fn paths_reject_substitution_and_credentials_and_requests_release_unexpected_fds() {
        let _exclusive = crate::FD_TESTS.lock().unwrap();
        // SAFETY: identity queries do not change process state.
        let (uid, pid) = unsafe { (libc::getuid(), libc::getpid()) };
        for value in [
            b"".as_slice(),
            b"0",
            b"-1",
            b"1x",
            b"4294967296",
            b"99999999999",
        ] {
            assert!(decimal(value).is_none());
        }
        assert_eq!(decimal(b"4294967295"), Some(u32::MAX));
        for path in [
            "/tmp/arbitrary",
            "/dev/dri/card",
            "/dev/dri/renderD128",
            "/dev/dri/card1/../x",
            "/dev/dri/card-1",
        ] {
            assert!(!card_path(Path::new(path)));
        }
        assert!(card_path(Path::new("/dev/dri/card123")));
        let mut template = b"/tmp/dpe-kms-XXXXXX\0".to_vec();
        // SAFETY: writable mkdtemp template, valid six-byte suffix.
        assert!(!unsafe { libc::mkdtemp(template.as_mut_ptr().cast()) }.is_null());
        let directory = CString::from_vec_with_nul(template).unwrap();
        let directory = Path::new(std::ffi::OsStr::from_bytes(directory.to_bytes()));
        let path = directory.join("socket");
        let listener = UnixListener::bind(&path).unwrap();
        assert!(socket_path(&path, uid));
        assert!(!socket_path(&path, uid.wrapping_add(1)));
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(!socket_path(&path, uid));
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::remove_file(&path).unwrap();
        std::os::unix::fs::symlink("/tmp/other", &path).unwrap();
        assert!(!socket_path(&path, uid));
        std::fs::remove_file(&path).unwrap();
        drop(listener);
        std::fs::remove_dir(directory).unwrap();
        let (left, right) = UnixDatagram::pair().unwrap();
        // A real seqpacket replacement is required; datagram peers are rejected.
        let send = |version: u32, kind: i32, descriptors: &[BorrowedFd<'_>]| {
            let mut bytes = [0u8; 12];
            bytes[..4].copy_from_slice(&version.to_ne_bytes());
            bytes[4..8].copy_from_slice(&kind.to_ne_bytes());
            wire::send_raw(left.as_fd(), &bytes, descriptors).unwrap();
        };
        let initial = std::fs::read_dir("/proc/self/fd").unwrap().count();
        for (version, kind, descriptors) in [
            (5, 0, vec![left.as_fd()]),
            (6, 1, vec![left.as_fd()]),
            (6, 0, vec![]),
            (6, 0, vec![left.as_fd(); 32]),
        ] {
            send(version, kind, &descriptors);
            assert!(request(right.as_fd(), true, uid, pid).is_err());
            assert_eq!(std::fs::read_dir("/proc/self/fd").unwrap().count(), initial);
        }
        send(6, 0, &[left.as_fd()]);
        assert!(request(right.as_fd(), true, uid, pid).is_err());
        send(6, 1, &[]);
        assert!(matches!(
            request(right.as_fd(), false, uid, pid),
            Ok(Request::Capture(None))
        ));
        for (kind, connector, valid) in [
            (1, 123u32, true),
            (ffi::DPE_KMS_SCOPE_REQUEST, 123, true),
            (ffi::DPE_KMS_SCOPE_REQUEST, 0, false),
            (ffi::DPE_KMS_SCOPE_REQUEST, u32::MAX, false),
        ] {
            let mut bytes = [0u8; 12];
            bytes[..4].copy_from_slice(&6u32.to_ne_bytes());
            bytes[4..8].copy_from_slice(&kind.to_ne_bytes());
            bytes[8..].copy_from_slice(&connector.to_ne_bytes());
            wire::send_raw(left.as_fd(), &bytes, &[]).unwrap();
            let result = request(right.as_fd(), false, uid, pid);
            if !valid {
                assert!(result.is_err());
            } else if kind == 1 {
                assert!(matches!(result, Ok(Request::Capture(None))));
            } else {
                assert!(matches!(result, Ok(Request::Capture(Some(123)))));
            }
        }
        let mut raw = [-1; 2];
        assert_eq!(
            // SAFETY: two-slot output and successful new seqpacket ownership.
            unsafe {
                libc::socketpair(
                    libc::AF_UNIX,
                    libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC,
                    0,
                    raw.as_mut_ptr(),
                )
            },
            0
        );
        let pair = raw.map(|raw| {
            // SAFETY: socketpair uniquely created each descriptor above.
            unsafe { OwnedFd::from_raw_fd(raw) }
        });
        for (wanted_uid, wanted_pid) in [(uid.wrapping_add(1), pid), (uid, pid + 1)] {
            send(6, 0, &[pair[0].as_fd()]);
            assert!(request(right.as_fd(), true, wanted_uid, wanted_pid).is_err());
        }
        send(6, 0, &[pair[0].as_fd()]);
        let Request::Replace(replacement) = request(right.as_fd(), true, uid, pid).unwrap() else {
            panic!("expected replacement");
        };
        assert_ne!(
            // SAFETY: F_GETFD only reads flags on the retained replacement fd.
            unsafe { libc::fcntl(replacement.as_raw_fd(), libc::F_GETFD) } & libc::FD_CLOEXEC,
            0
        );
        send(6, 1, &[pair[0].as_fd()]);
        assert!(request(right.as_fd(), false, uid, pid).is_err());
    }
}
