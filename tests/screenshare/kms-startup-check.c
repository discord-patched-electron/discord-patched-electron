// SPDX-License-Identifier: GPL-3.0-only
// Exercise real startup/cleanup over local sockets, replacing only OS privilege
// and exec boundaries. Never invokes pkexec/GSR, opens DRM or captures pixels.
#define _GNU_SOURCE
#include "../../integration/video/kms-client.h"
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/capability.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static bool blocked, authorized, pkexec_missing, scoped, grant_valid = true, unsafe_ancestor;
static enum {
  GOOD,
  DENIED,
  CANCELLED,
  UNAVAILABLE,
  INTERRUPTED,
  BAD_UID,
  BAD_PID,
  SCOPED,
  BAD_CAPABILITY
} mode;
static const char *helper = "/usr/local/libexec/discord-patched-electron/kms-server",
                  *pkexec = "/usr/bin/pkexec";
static const char *scoped_root = "/usr/local/libexec/discord-patched-electron/";
int __wrap_lstat(const char *file, struct stat *st) {
  if (!strcmp(file, "/") || !strcmp(file, "/usr") || !strcmp(file, "/usr/local") ||
      !strcmp(file, "/usr/local/libexec") ||
      !strcmp(file, "/usr/local/libexec/discord-patched-electron") ||
      !strcmp(file, "/usr/local/libexec/discord-patched-electron/permissions")) {
    *st = (struct stat){.st_mode = S_IFDIR | 0755, .st_uid = 0};
    if (unsafe_ancestor && !strcmp(file, "/usr/local"))
      st->st_mode |= S_IWOTH;
    return 0;
  }
  bool local = !strncmp(file, scoped_root, strlen(scoped_root));
  assert(local || !strcmp(file, helper) || !strcmp(file, pkexec) ||
         !strcmp(file, "/usr/bin/pkcheck"));
  if (local && !scoped) {
    errno = ENOENT;
    return -1;
  }
  if (pkexec_missing && !strcmp(file, pkexec)) {
    errno = ENOENT;
    return -1;
  }
  *st = (struct stat){.st_mode = S_IFREG | 0755, .st_uid = 0};
  return 0;
}
int __wrap_access(const char *file, int access_mode) {
  assert((!strcmp(file, helper) || !strcmp(file, pkexec) || !strcmp(file, "/usr/bin/pkcheck") ||
          !strncmp(file, scoped_root, strlen(scoped_root))) &&
         access_mode == X_OK);
  return 0;
}
FILE *__wrap_fopen(const char *file, const char *mode) {
  assert(scoped && !strncmp(file, scoped_root, strlen(scoped_root)) && !strcmp(mode, "re"));
  static char marker[] = "DPE-KMS-3\n1\n2\n3\n4\n5\n/fixture/discord\n";
  return fmemopen(marker, strlen(marker), "r");
}
int __wrap_prctl(int option, ...) {
  assert(option == PR_GET_NO_NEW_PRIVS);
  return blocked;
}
int __real_getsockopt(int, int, int, void *, socklen_t *);
int __wrap_getsockopt(int fd, int level, int option, void *value, socklen_t *length) {
  int result = __real_getsockopt(fd, level, option, value, length);
  if (!result && level == SOL_SOCKET && option == SO_PEERCRED) {
    struct ucred *peer = value;
    if (authorized || scoped)
      peer->uid = 0;
    if (mode == BAD_UID)
      peer->uid = 12345;
    if (mode == BAD_PID)
      peer->pid++;
  }
  return result;
}
int __real_kill(pid_t, int);
int __wrap_kill(pid_t pid, int signal) {
  if ((authorized || scoped) && signal == SIGKILL) {
    errno = EPERM;
    return -1;
  }
  return __real_kill(pid, signal);
}
static int fixture_exec(const char *file, char *const argv[], char *const env[]) {
  if (!strcmp(file, "/usr/bin/pkcheck")) {
    assert(scoped && !strcmp(argv[1], "--action-id") &&
           !strcmp(argv[2], "org.discord-patched-electron.capture") &&
           !strcmp(argv[3], "--process") && !argv[5]);
    _exit(grant_valid ? 0 : 1);
  }
  struct stat output, error;
  assert(!fstat(STDOUT_FILENO, &output) && !fstat(STDERR_FILENO, &error));
  assert(output.st_dev == error.st_dev && output.st_ino == error.st_ino);
  char unused;
  assert(read(STDIN_FILENO, &unused, 1) == 0);
  assert(!strcmp(env[0], "PATH=/usr/bin") && !strcmp(env[1], "LANG=C") && !env[2]);
  const char *socket_path;
  if (authorized || scoped) {
    assert(!strcmp(file, pkexec) && !strcmp(argv[0], pkexec));
    assert(!strcmp(argv[1], "--disable-internal-agent") &&
           !strcmp(argv[2],
                   scoped ? "/usr/local/libexec/discord-patched-electron/kms-server" : helper));
    assert(!strcmp(argv[4], "/dev/dri/card999") && !strcmp(argv[5], "/fixture/auth") &&
           !strcmp(argv[6], "fixture-token") && !argv[7]);
    socket_path = argv[3];
  } else {
    assert(!strcmp(file, helper) && !strcmp(argv[0], helper));
    assert(!strcmp(argv[2], "/dev/dri/card999") && !argv[3]);
    socket_path = argv[1];
  }
  if (mode == CANCELLED)
    _exit(126);
  if (mode == UNAVAILABLE)
    _exit(127);
  if (mode == INTERRUPTED) {
    __real_kill(getppid(), SIGTERM);
    _exit(0);
  }
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  snprintf(address.sun_path, sizeof(address.sun_path), "%s", socket_path);
  assert(!connect(fd, (void *)&address, sizeof(address)));
  gsr_kms_request request;
  char control[CMSG_SPACE(sizeof(int))];
  struct iovec vector = {.iov_base = &request, .iov_len = sizeof(request)};
  struct msghdr message = {.msg_iov = &vector,
                           .msg_iovlen = 1,
                           .msg_control = control,
                           .msg_controllen = sizeof(control)};
  ssize_t size = recvmsg(fd, &message, 0);
  if (mode == BAD_UID || mode == BAD_PID) {
    assert(size == 0);
    close(fd);
    _exit(0);
  }
  assert(size == sizeof(request) && request.type == KMS_REQUEST_TYPE_REPLACE_CONNECTION);
  struct cmsghdr *header = CMSG_FIRSTHDR(&message);
  assert(header && header->cmsg_type == SCM_RIGHTS);
  int connection;
  memcpy(&connection, CMSG_DATA(header), sizeof(connection));
  close(fd);
  gsr_kms_response response = {.version = 6};
  if (mode == SCOPED || mode == BAD_CAPABILITY)
    strcpy(response.err_msg,
           mode == SCOPED ? DPE_KMS_SCOPE_CAPABILITY : DPE_KMS_SCOPE_CAPABILITY "extra");
  assert(send(connection, &response, sizeof(response), MSG_NOSIGNAL) == sizeof(response));
  assert(recv(connection, &request, sizeof(request), 0) == sizeof(request));
  assert(request.type == (mode == SCOPED ? (int)DPE_KMS_SCOPE_REQUEST : KMS_REQUEST_TYPE_GET_KMS));
  assert(request.new_connection_fd == (mode == SCOPED ? 432 : -1)); // Access checked before ready.
  if (mode == DENIED) {
    response.result = KMS_RESULT_FAILED_TO_GET_PLANE;
    strcpy(response.err_msg, "drmfb handle is NULL");
  }
  assert(send(connection, &response, sizeof(response), MSG_NOSIGNAL) == sizeof(response));
  assert(recv(connection, &request, sizeof(request), 0) == 0); // Socket close stops root helper.
  close(connection);
  _exit(0);
}
int __real_posix_spawn(pid_t *, const char *, const posix_spawn_file_actions_t *,
                       const posix_spawnattr_t *, char *const[], char *const[]);
int __wrap_posix_spawn(pid_t *pid, const char *path, const posix_spawn_file_actions_t *actions,
                       const posix_spawnattr_t *attributes, char *const argv[], char *const env[]) {
  assert(actions && !strcmp(env[0], "PATH=/usr/bin") && !strcmp(env[1], "LANG=C") && !env[2]);
  assert(!strcmp(path, pkexec) || !strcmp(path, "/usr/bin/pkcheck"));
  char state[96];
  snprintf(state, sizeof(state), "DPE_KMS_FIXTURE=%d,%d,%d,%d", mode, authorized, scoped,
           grant_valid);
  char *environment[] = {env[0], env[1], state, NULL};
  return __real_posix_spawn(pid, "/proc/self/exe", actions, attributes, argv, environment);
}
static void attempt(bool success, const char *error) {
  struct dpe_kms_client client;
  assert(dpe_kms_client_open(&client, "/dev/dri/card999", authorized) == success);
  assert(error ? client.error && !strcmp(client.error, error) : !client.error);
  dpe_kms_client_close(&client);
  assert(client.pid == -1 && client.fd == -1 && !client.owner);
  dpe_kms_client_close(&client); // Clearing the opaque owner makes repeated cleanup harmless.
  assert(waitpid(-1, NULL, WNOHANG) == -1 && errno == ECHILD);
}
static void attempt_output(bool scoped_expected) {
  struct dpe_kms_client client;
  struct dpe_kms_output output = {.connector = 432};
  strcpy(output.card, "/dev/dri/card999");
  assert(dpe_kms_client_open_output(&client, &output, false));
  assert(client.connector == (scoped_expected ? 432u : 0u));
  dpe_kms_client_close(&client);
  assert(client.fd == -1 && !client.owner && !client.connector);
}
int main(int argc, char **argv) {
  (void)argc;
  const char *state = getenv("DPE_KMS_FIXTURE");
  if (state) {
    int saved_mode, saved_authorized, saved_scoped, saved_valid;
    assert(sscanf(state, "%d,%d,%d,%d", &saved_mode, &saved_authorized, &saved_scoped,
                  &saved_valid) == 4);
    mode = saved_mode;
    authorized = saved_authorized;
    scoped = saved_scoped;
    grant_valid = saved_valid;
    char *environment[] = {"PATH=/usr/bin", "LANG=C", NULL};
    return fixture_exec(argv[0], argv, environment);
  }
  setenv("DPE_KMS_AUTH_SOCKET", "/fixture/auth", 1);
  setenv("DPE_KMS_AUTH_TOKEN", "fixture-token", 1);
  assert(dpe_kms_authorization_required() && !dpe_kms_helper_status());
  attempt(false, "permission-required");
  authorized = true;
  attempt(false, "permission-required"); // Legacy flag cannot bypass Settings permission.
  authorized = false;
  scoped = true;
  assert(!dpe_kms_authorization_required());
  attempt(true, NULL);
  attempt_output(false); // Old helper: no extension without an exact advertisement.
  mode = SCOPED;
  attempt_output(true);
  mode = BAD_CAPABILITY;
  attempt_output(false);
  mode = DENIED;
  attempt(false, "permission-blocked");
  mode = CANCELLED;
  attempt(false, "permission-required");
  mode = UNAVAILABLE;
  attempt(false, "permission-required");
  mode = INTERRUPTED;
  attempt(false, "authorization-cancelled");
  mode = BAD_UID;
  attempt(false, "unsupported");
  mode = BAD_PID;
  attempt(false, "unsupported");
  pkexec_missing = true;
  attempt(false, "authorization-unavailable");
  pkexec_missing = false;
  blocked = true;
  attempt(false, "permission-blocked");
  blocked = false;
  mode = GOOD;
  grant_valid = false;
  attempt(false, "permission-required");
  grant_valid = true;
  unsafe_ancestor = true;
  assert(dpe_kms_authorization_required());
  attempt(false, "permission-required");
  puts("KMS explicit authorization, early access check, peer authentication and cancellation "
       "passed");
}
