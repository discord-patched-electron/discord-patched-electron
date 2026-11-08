// SPDX-License-Identifier: GPL-3.0-only
//! Permission checks and bounded startup. Only the dedicated installed helper
//! can run. Owned sessions, spawn actions and sockets unwind on every failure;
//! Rust never runs user-space code between fork and exec.
use crate::{ffi, transport};
use dpe_core::handles::Handles;
use dpe_os::wire;
use std::{
    ffi::{CStr, CString},
    mem::MaybeUninit,
    os::{
        fd::{AsFd, AsRawFd, FromRawFd, OwnedFd},
        unix::{ffi::OsStrExt, net::UnixListener},
    },
    ptr,
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
const PKEXEC: &CStr = c"/usr/bin/pkexec";
const PKCHECK: &CStr = c"/usr/bin/pkcheck";
const HELPER: &CStr = c"/usr/local/libexec/discord-patched-electron/kms-server";
const ROOT: &str = "/usr/local/libexec/discord-patched-electron";
type Result<T> = std::result::Result<T, &'static CStr>;
const UNSUPPORTED: &CStr = c"unsupported";
static OPENING: Mutex<()> = Mutex::new(());
static CANCELLED: AtomicBool = AtomicBool::new(false);
static CHILDREN: AtomicUsize = AtomicUsize::new(0);
static REAPER_FAILED: AtomicBool = AtomicBool::new(false);
fn errno() -> i32 {
    std::io::Error::last_os_error().raw_os_error().unwrap_or(0)
}
fn stat(path: &CStr) -> Option<libc::stat> {
    let mut value = MaybeUninit::uninit();
    // SAFETY: C string and output storage are live; success initializes stat.
    if unsafe { libc::lstat(path.as_ptr(), value.as_mut_ptr()) } != 0 {
        return None;
    }
    // SAFETY: lstat succeeded, initializing every field read by this module.
    Some(unsafe { value.assume_init() })
}
fn trusted(value: &libc::stat, kind: libc::mode_t) -> bool {
    value.st_mode & libc::S_IFMT == kind && value.st_uid == 0 && value.st_mode & 0o022 == 0
}
fn executable(path: &CStr) -> bool {
    stat(path).is_some_and(|s| trusted(&s, libc::S_IFREG))
        // SAFETY: access only reads the given NUL-terminated path.
        && unsafe { libc::access(path.as_ptr(), libc::X_OK) } == 0
}
pub(crate) fn ancestors(path: &CStr) -> bool {
    let mut parent = path.to_bytes().to_vec();
    loop {
        let Some(separator) = parent.iter().rposition(|byte| *byte == b'/') else {
            return false;
        };
        parent.truncate(if separator == 0 { 1 } else { separator });
        let Ok(path) = CString::new(parent.as_slice()) else {
            return false;
        };
        if !stat(&path).is_some_and(|s| trusted(&s, libc::S_IFDIR)) {
            return false;
        }
        if separator == 0 {
            return true;
        }
    }
}
fn permission() -> bool {
    // SAFETY: getuid has no pointer arguments or side effects.
    let uid = unsafe { libc::getuid() };
    let Ok(marker) = CString::new(format!("{ROOT}/permissions/{uid}")) else {
        return false;
    };
    if !stat(HELPER).is_some_and(|s| trusted(&s, libc::S_IFREG) && s.st_mode & libc::S_IXUSR != 0)
        || !ancestors(HELPER)
        || !ancestors(&marker)
        || !stat(&marker).is_some_and(|s| trusted(&s, libc::S_IFREG))
    {
        return false;
    }
    // SAFETY: valid C strings; this owns FILE until the matching fclose.
    let file = unsafe { libc::fopen(marker.as_ptr(), c"re".as_ptr()) };
    if file.is_null() {
        return false;
    }
    let mut bytes = [0; 4096];
    // SAFETY: live file and bounded writable storage of the declared length.
    let size = unsafe { libc::fread(bytes.as_mut_ptr().cast(), 1, bytes.len(), file) };
    // SAFETY: close the uniquely owned successful fopen result once.
    unsafe {
        libc::fclose(file);
    }
    crate::permission::Grant::parse(&bytes[..size]).is_some()
}
pub(crate) fn helper_status() -> Option<&'static CStr> {
    // SAFETY: PR_GET_NO_NEW_PRIVS reads this process state and takes no pointers.
    if unsafe { libc::prctl(libc::PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) } != 0 {
        Some(c"permission-blocked")
    } else if !executable(PKEXEC) || !executable(PKCHECK) {
        Some(c"authorization-unavailable")
    } else {
        None
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_kms_authorization_required() -> bool {
    !permission()
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_kms_helper_status() -> *const libc::c_char {
    helper_status().map_or(ptr::null(), CStr::as_ptr)
}
struct Actions(libc::posix_spawn_file_actions_t);
impl Actions {
    fn new(quiet: bool) -> Option<Self> {
        let mut storage = MaybeUninit::uninit();
        // SAFETY: init receives correctly aligned output storage.
        if unsafe { libc::posix_spawn_file_actions_init(storage.as_mut_ptr()) } != 0 {
            return None;
        }
        // SAFETY: successful init created the owned spawn-actions object.
        let mut owner = Self(unsafe { storage.assume_init() });
        // SAFETY: actions is initialized, null path is static, and every added
        // action is executed by libc in the new process, never by Rust post-fork.
        let valid = unsafe {
            libc::posix_spawn_file_actions_addopen(
                &mut owner.0,
                0,
                c"/dev/null".as_ptr(),
                libc::O_RDONLY,
                0,
            ) == 0
                && if quiet {
                    libc::posix_spawn_file_actions_addopen(
                        &mut owner.0,
                        1,
                        c"/dev/null".as_ptr(),
                        libc::O_WRONLY,
                        0,
                    ) == 0
                        && libc::posix_spawn_file_actions_adddup2(&mut owner.0, 1, 2) == 0
                } else {
                    libc::posix_spawn_file_actions_adddup2(&mut owner.0, 2, 1) == 0
                }
        };
        valid.then_some(owner)
    }
}
impl Drop for Actions {
    fn drop(&mut self) {
        // SAFETY: this owner holds one initialized spawn-actions object.
        unsafe {
            libc::posix_spawn_file_actions_destroy(&mut self.0);
        }
    }
}
/// The standalone producer owns SIGCHLD and all waits on these children. No
/// handler may reap them externally; reject inherited automatic child reaping.
struct Child {
    pid: libc::pid_t,
    permit: Option<ChildPermit>,
}
struct ChildPermit;
impl ChildPermit {
    fn reserve() -> Option<Self> {
        if REAPER_FAILED.load(Ordering::Relaxed) {
            return None;
        }
        CHILDREN
            .try_update(Ordering::Relaxed, Ordering::Relaxed, |count| {
                (count < 64).then_some(count + 1)
            })
            .ok()
            .map(|_| Self)
    }
}
impl Drop for ChildPermit {
    fn drop(&mut self) {
        CHILDREN.fetch_sub(1, Ordering::Relaxed);
    }
}
impl Child {
    fn spawn(args: &[&CStr], quiet: bool) -> Option<Self> {
        let permit = ChildPermit::reserve()?;
        let mut disposition = MaybeUninit::<libc::sigaction>::uninit();
        // SAFETY: read the existing SIGCHLD disposition into local storage.
        if unsafe { libc::sigaction(libc::SIGCHLD, ptr::null(), disposition.as_mut_ptr()) } != 0 {
            return None;
        }
        // SAFETY: the successful query initialized the disposition.
        let disposition = unsafe { disposition.assume_init() };
        if disposition.sa_sigaction != libc::SIG_DFL
            || disposition.sa_flags & libc::SA_NOCLDWAIT != 0
        {
            return None;
        }
        let actions = Actions::new(quiet)?;
        let mut argv: Vec<_> = args.iter().map(|arg| arg.as_ptr().cast_mut()).collect();
        argv.push(ptr::null_mut());
        let environment = [
            c"PATH=/usr/bin".as_ptr().cast_mut(),
            c"LANG=C".as_ptr().cast_mut(),
            ptr::null_mut(),
        ];
        let mut pid = -1;
        // SAFETY: arrays are NUL-terminated and remain live through synchronous
        // posix_spawn. libc performs all child setup; no Rust closure is forked.
        let result = unsafe {
            libc::posix_spawn(
                &mut pid,
                args.first()?.as_ptr(),
                &actions.0,
                ptr::null(),
                argv.as_ptr(),
                environment.as_ptr(),
            )
        };
        if result == 0 && pid > 0 {
            Some(Self {
                pid,
                permit: Some(permit),
            })
        } else {
            None
        }
    }
    fn poll(&mut self) -> Option<i32> {
        if self.pid <= 0 {
            return Some(-1);
        }
        let mut status = 0;
        // SAFETY: this owner is the sole waiter for its unreaped child.
        let result = unsafe { libc::waitpid(self.pid, &mut status, libc::WNOHANG) };
        if result == self.pid {
            self.pid = -1;
            Some(status)
        } else if result < 0 && errno() != libc::EINTR {
            self.pid = -1;
            Some(-1)
        } else {
            None
        }
    }
}
impl Drop for Child {
    fn drop(&mut self) {
        if self.poll().is_some() {
            return;
        }
        // SAFETY: the retained PID identifies this owner's unreaped child;
        // privileged helpers may reject the signal but exit on socket close.
        unsafe {
            libc::kill(self.pid, libc::SIGKILL);
        }
        for _ in 0..20 {
            if self.poll().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        // A stuck privileged child must not block source teardown. Its wait is
        // moved to a reaper; the callback/session owns no descriptors by now.
        let pid = self.pid;
        self.pid = -1;
        let permit = self.permit.take();
        let reaper = std::thread::Builder::new()
            .name("dpe-kms-reap".into())
            .spawn(move || {
                let _permit = permit;
                // SAFETY: the sole wait ownership moved here, without sending any
                // later signal that could target a recycled PID.
                while unsafe { libc::waitpid(pid, ptr::null_mut(), 0) } < 0
                    && errno() == libc::EINTR
                {}
            });
        if reaper.is_err() {
            // Do not admit further children if the OS cannot create the one
            // bounded reaper needed to preserve teardown and wait ownership.
            REAPER_FAILED.store(true, Ordering::Relaxed);
        }
    }
}
fn authorized() -> bool {
    if !executable(PKCHECK) || !executable(PKEXEC) {
        return false;
    }
    // SAFETY: getpid queries the current live process identity.
    let process = CString::new(unsafe { libc::getpid() }.to_string()).expect("decimal PID");
    let Some(mut child) = Child::spawn(
        &[
            PKCHECK,
            c"--action-id",
            c"org.discord-patched-electron.capture",
            c"--process",
            &process,
        ],
        true,
    ) else {
        return false;
    };
    let deadline = Instant::now() + Duration::from_secs(1);
    while Instant::now() < deadline {
        if let Some(status) = child.poll() {
            return status >= 0 && libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    false
}
extern "C" fn cancel(_: i32) {
    CANCELLED.store(true, Ordering::Relaxed);
}
struct Signals {
    term: libc::sigaction,
    interrupt: libc::sigaction,
}
impl Signals {
    fn install() -> Option<Self> {
        // SAFETY: zero-initialized sigaction admits all its field values.
        let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
        action.sa_sigaction = cancel as *const () as usize;
        // SAFETY: initialize the valid sigset_t field in place.
        unsafe {
            libc::sigemptyset(&mut action.sa_mask);
        }
        let mut term = MaybeUninit::uninit();
        let mut interrupt = MaybeUninit::uninit();
        CANCELLED.store(false, Ordering::Relaxed);
        // SAFETY: handler only stores a lock-free atomic; outputs hold previous
        // dispositions. OPENING serializes all installs in this process.
        unsafe {
            if libc::sigaction(libc::SIGTERM, &action, term.as_mut_ptr()) != 0 {
                return None;
            }
            let term = term.assume_init();
            if libc::sigaction(libc::SIGINT, &action, interrupt.as_mut_ptr()) != 0 {
                libc::sigaction(libc::SIGTERM, &term, ptr::null_mut());
                return None;
            }
            Some(Self {
                term,
                interrupt: interrupt.assume_init(),
            })
        }
    }
}
impl Drop for Signals {
    fn drop(&mut self) {
        // SAFETY: restore both valid saved dispositions before releasing OPENING.
        unsafe {
            libc::sigaction(libc::SIGTERM, &self.term, ptr::null_mut());
            libc::sigaction(libc::SIGINT, &self.interrupt, ptr::null_mut());
        }
    }
}
struct Directory(CString);
impl Directory {
    fn new() -> Option<Self> {
        let mut template = b"/tmp/dpe-kms-XXXXXX\0".to_vec();
        // SAFETY: writable NUL-terminated template with six required X bytes.
        if unsafe { libc::mkdtemp(template.as_mut_ptr().cast()) }.is_null() {
            return None;
        }
        Some(Self(CString::from_vec_with_nul(template).ok()?))
    }
    fn socket(&self) -> CString {
        let mut path = self.0.to_bytes().to_vec();
        path.extend_from_slice(b"/socket");
        CString::new(path).expect("generated socket path")
    }
}
impl Drop for Directory {
    fn drop(&mut self) {
        let socket = self.socket();
        // SAFETY: owned temporary directory and its fixed socket path. Unlink
        // never follows a substituted symlink, and rmdir removes no contents.
        unsafe {
            libc::unlink(socket.as_ptr());
            libc::rmdir(self.0.as_ptr());
        }
    }
}
fn readable(fd: i32, millis: i32) -> bool {
    let mut poll = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    // SAFETY: poll borrows the live descriptor and writes one local record.
    unsafe { libc::poll(&mut poll, 1, millis) == 1 && poll.revents & libc::POLLIN != 0 }
}
fn socket_pair() -> Option<[OwnedFd; 2]> {
    let mut raw = [-1; 2];
    // SAFETY: exact two-fd output. Success creates unique CLOEXEC descriptors.
    if unsafe {
        libc::socketpair(
            libc::AF_UNIX,
            libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC,
            0,
            raw.as_mut_ptr(),
        )
    } != 0
    {
        return None;
    }
    Some(raw.map(|fd| {
        // SAFETY: successful socketpair transferred unique fd ownership.
        unsafe { OwnedFd::from_raw_fd(fd) }
    }))
}
pub(crate) struct Session {
    socket: Option<OwnedFd>,
    child: Child,
    connector: u32,
}
impl Session {
    pub(crate) fn socket(&self) -> Option<std::os::fd::BorrowedFd<'_>> {
        self.socket.as_ref().map(AsFd::as_fd)
    }
    pub(crate) fn connector(&self) -> u32 {
        self.connector
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        drop(self.socket.take());
    } // Close before waiting on root.
}
fn sessions() -> &'static Handles<Session> {
    static TABLE: OnceLock<Handles<Session>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::new(32))
}
pub(crate) fn open(card: &CStr, connector: u32) -> Result<Session> {
    let _exclusive = OPENING.try_lock().map_err(|_| UNSUPPORTED)?;
    if let Some(status) = helper_status() {
        return Err(status);
    }
    if !permission() || !authorized() {
        return Err(c"permission-required");
    }
    let directory = Directory::new().ok_or(UNSUPPORTED)?;
    let path = directory.socket();
    let listener = UnixListener::bind(std::ffi::OsStr::from_bytes(path.to_bytes()))
        .map_err(|_| UNSUPPORTED)?;
    listener.set_nonblocking(true).map_err(|_| UNSUPPORTED)?;
    let _signals = Signals::install().ok_or(UNSUPPORTED)?;
    let endpoint = CString::new(
        std::env::var("DPE_KMS_AUTH_SOCKET").map_err(|_| c"permission-required" as &CStr)?,
    )
    .map_err(|_| UNSUPPORTED)?;
    let token = CString::new(
        std::env::var("DPE_KMS_AUTH_TOKEN").map_err(|_| c"permission-required" as &CStr)?,
    )
    .map_err(|_| UNSUPPORTED)?;
    let child = Child::spawn(
        &[
            PKEXEC,
            c"--disable-internal-agent",
            HELPER,
            &path,
            card,
            &endpoint,
            &token,
        ],
        false,
    )
    .ok_or(UNSUPPORTED)?;
    let mut session = Session {
        socket: None,
        child,
        connector: 0,
    };
    let deadline = Instant::now() + Duration::from_millis(1500);
    loop {
        if CANCELLED.load(Ordering::Relaxed) {
            return Err(c"authorization-cancelled");
        }
        if Instant::now() >= deadline {
            return Err(UNSUPPORTED);
        }
        if readable(listener.as_raw_fd(), 100) {
            break;
        }
        if let Some(status) = session.child.poll() {
            return Err(
                if status >= 0
                    && libc::WIFEXITED(status)
                    && matches!(libc::WEXITSTATUS(status), 126 | 127)
                {
                    c"permission-required"
                } else if CANCELLED.load(Ordering::Relaxed) {
                    c"authorization-cancelled"
                } else {
                    UNSUPPORTED
                },
            );
        }
    }
    let (accepted, _) = listener.accept().map_err(|_| UNSUPPORTED)?;
    // SAFETY: plain integer credential record, writable through getsockopt.
    let mut peer: libc::ucred = unsafe { std::mem::zeroed() };
    let mut length = size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: accepted remains owned, output lengths match the credential record.
    if unsafe {
        libc::getsockopt(
            accepted.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&raw mut peer).cast(),
            &mut length,
        )
    } != 0
        || length as usize != size_of::<libc::ucred>()
        || peer.uid != 0
        || peer.pid != session.child.pid
    {
        return Err(UNSUPPORTED);
    }
    let [socket, replacement] = socket_pair().ok_or(UNSUPPORTED)?;
    let mut request = [0u8; 12];
    request[..4].copy_from_slice(&6u32.to_ne_bytes());
    request[8..].copy_from_slice(&replacement.as_raw_fd().to_ne_bytes());
    wire::send_raw(accepted.as_fd(), &request, &[replacement.as_fd()]).map_err(|_| UNSUPPORTED)?;
    drop(replacement);
    drop(accepted);
    let mut response = ffi::gsr_kms_response::default();
    if !readable(socket.as_raw_fd(), 500) {
        return Err(UNSUPPORTED);
    }
    // SAFETY: socket retained and response is exclusive, initialized output.
    if unsafe { transport::dpe_kms_receive(socket.as_raw_fd(), &mut response) } != 1 {
        return Err(UNSUPPORTED);
    }
    let empty = response.num_items == 0;
    if response
        .err_msg
        .iter()
        .map(|byte| *byte as u8)
        .take_while(|byte| *byte != 0)
        .eq(crate::export::SCOPED_CAPABILITY.iter().copied())
    {
        session.connector = connector;
    }
    // SAFETY: close all received descriptors, including unexpected handshake data.
    unsafe {
        transport::dpe_kms_response_close(&mut response);
    }
    if !empty {
        return Err(UNSUPPORTED);
    }
    let client = ffi::dpe_kms_client {
        fd: socket.as_raw_fd(),
        pid: session.child.pid,
        error: ptr::null(),
        owner: 0,
        connector: session.connector,
    };
    // SAFETY: client borrows socket synchronously; no response descriptors remain.
    if !unsafe { transport::dpe_kms_request(&client as *const _ as *mut _) }
        || !readable(socket.as_raw_fd(), 500)
    {
        return Err(UNSUPPORTED);
    }
    // SAFETY: initialized exclusive response and a retained socket.
    let result = unsafe { transport::dpe_kms_receive(socket.as_raw_fd(), &mut response) };
    // SAFETY: release any successful access-check descriptors before ready.
    unsafe {
        transport::dpe_kms_response_close(&mut response);
    }
    if result != 1 {
        return Err(if result == -2 {
            c"permission-blocked"
        } else {
            UNSUPPORTED
        });
    }
    if CANCELLED.load(Ordering::Relaxed) {
        return Err(c"authorization-cancelled");
    }
    session.socket = Some(socket);
    Ok(session)
}
/// The caller provides exclusive writable client storage.
unsafe fn publish(client: *mut ffi::dpe_kms_client, result: Result<Session>) -> bool {
    if client.is_null() {
        return false;
    }
    let mut value = ffi::dpe_kms_client {
        fd: -1,
        pid: -1,
        error: UNSUPPORTED.as_ptr(),
        owner: 0,
        connector: 0,
    };
    match result {
        Ok(session) => {
            let fd = session.socket.as_ref().expect("ready socket").as_raw_fd();
            let pid = session.child.pid;
            let connector = session.connector;
            if let Ok(token) = sessions().insert(session) {
                value = ffi::dpe_kms_client {
                    fd,
                    pid,
                    error: ptr::null(),
                    owner: token.get() as _,
                    connector,
                };
            }
        }
        Err(error) => value.error = error.as_ptr(),
    }
    let success = value.owner != 0;
    // SAFETY: caller supplies exclusive writable client storage.
    unsafe {
        client.write(value);
    }
    success
}
/// # Safety
/// Client points to writable uninitialized storage. Card is a live C string.
/// The standalone producer must own SIGCHLD and waits on its spawned children.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_client_open(
    client: *mut ffi::dpe_kms_client,
    card: *const libc::c_char,
    _authorize: bool,
) -> bool {
    if client.is_null() {
        return false;
    }
    let result = if card.is_null() {
        Err(UNSUPPORTED)
    } else {
        // SAFETY: caller retains the NUL-terminated card path for this call.
        open(unsafe { CStr::from_ptr(card) }, 0)
    };
    // SAFETY: caller provides exclusive client output storage.
    unsafe { publish(client, result) }
}
/// # Safety
/// Output points to an initialized monitor description; client is writable.
/// The standalone producer exclusively owns SIGCHLD and its children.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_client_open_output(
    client: *mut ffi::dpe_kms_client,
    output: *const ffi::dpe_kms_output,
    _authorize: bool,
) -> bool {
    if client.is_null() {
        return false;
    }
    let result = (|| {
        if output.is_null() {
            return Err(UNSUPPORTED);
        }
        // SAFETY: caller provides one initialized description. Copy just the
        // integer/string fields; no borrowed description escapes this call.
        let (card, connector) = unsafe { ((*output).card, (*output).connector) };
        let bytes = card.map(|byte| byte as u8);
        let card = CStr::from_bytes_until_nul(&bytes).map_err(|_| UNSUPPORTED)?;
        if connector == 0 || connector == u32::MAX {
            return Err(UNSUPPORTED);
        }
        open(card, connector)
    })();
    // SAFETY: caller provides exclusive client output storage.
    unsafe { publish(client, result) }
}
/// # Safety
/// Client points to an initialized client record, exclusively owned by caller.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_client_close(client: *mut ffi::dpe_kms_client) {
    if client.is_null() {
        return;
    }
    // SAFETY: caller owns this record; clear token and borrowed fields before
    // dropping the Rust owner, so duplicate/stale closes never close reused fds.
    let client = unsafe { &mut *client };
    let token = std::mem::replace(&mut client.owner, 0);
    client.fd = -1;
    client.pid = -1;
    client.connector = 0;
    drop(sessions().remove(token as usize));
}
