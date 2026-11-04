// SPDX-License-Identifier: GPL-3.0-only
// Narrow client for DPE's bundled server and explicitly installed polkit grant.
#define _GNU_SOURCE
#include "kms-client.h"
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
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
#include <xf86drm.h>
#include <xf86drmMode.h>

#define PKEXEC "/usr/bin/pkexec"
#define PKCHECK "/usr/bin/pkcheck"
#define ACTION "org.discord-patched-electron.capture"
#define SCOPED_ROOT "/usr/local/libexec/discord-patched-electron"
#define SCOPED_HELPER SCOPED_ROOT "/kms-server"
_Static_assert(sizeof(gsr_kms_request) == 12, "GSR protocol request ABI");
_Static_assert(sizeof(gsr_kms_response_item) == 160, "GSR protocol item ABI");
_Static_assert(sizeof(gsr_kms_response) == 1424 && offsetof(gsr_kms_response, items) == 136 &&
                   offsetof(gsr_kms_response, num_items) == 1416,
               "GSR protocol response ABI");

static bool trusted_executable(const char *file) {
  struct stat st;
  return !lstat(file, &st) && S_ISREG(st.st_mode) && !st.st_uid &&
         !(st.st_mode & (S_IWGRP | S_IWOTH)) && !access(file, X_OK);
}
static bool trusted_ancestors(const char *file) {
  char parent[PATH_MAX];
  if (strlen(file) >= sizeof(parent))
    return false;
  strcpy(parent, file);
  for (;;) {
    char *separator = strrchr(parent, '/');
    if (!separator)
      return false;
    bool root = separator == parent;
    separator[root ? 1 : 0] = '\0';
    struct stat info;
    if (lstat(parent, &info) || !S_ISDIR(info.st_mode) || info.st_uid ||
        (info.st_mode & (S_IWGRP | S_IWOTH)))
      return false;
    if (root)
      return true;
  }
}
static bool scoped_permission(void) {
  char marker[128];
  snprintf(marker, sizeof(marker), SCOPED_ROOT "/permissions/%u", (unsigned)getuid());
  if (!trusted_executable(SCOPED_HELPER) || !trusted_ancestors(SCOPED_HELPER) ||
      !trusted_ancestors(marker))
    return false;
  struct stat info;
  if (lstat(marker, &info) || !S_ISREG(info.st_mode) || info.st_uid ||
      (info.st_mode & (S_IWGRP | S_IWOTH)))
    return false;
  FILE *file = fopen(marker, "re");
  if (!file)
    return false;
  char contents[16] = {0};
  size_t size = fread(contents, 1, sizeof(contents), file);
  fclose(file);
  return size == 10 && !memcmp(contents, "DPE-KMS-2\n", 10);
}
bool dpe_kms_authorization_required(void) { return !scoped_permission(); }
const char *dpe_kms_helper_status(void) {
  // Neither file capabilities nor pkexec can elevate a no-new-privileges
  // process. Do not disable that restriction or offer a password loop.
  if (prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 0)
    return "permission-blocked";
  if (!trusted_executable(PKEXEC) || !trusted_executable(PKCHECK))
    return "authorization-unavailable";
  return NULL;
}
// No interaction flag is passed to pkcheck. Confirm that the installed rule
// applies before invoking pkexec automatically; stale grants must not open a
// surprise password prompt in Discord's picker. Our own live PID cannot be
// reused while this function waits, and is never supplied by another process.
static bool scoped_authorized(void) {
  if (!trusted_executable(PKCHECK) || !trusted_executable(PKEXEC))
    return false;
  char process[32];
  snprintf(process, sizeof(process), "%ld", (long)getpid());
  pid_t child = fork();
  if (child < 0)
    return false;
  if (!child) {
    int nullfd = open("/dev/null", O_RDWR | O_CLOEXEC);
    if (nullfd < 0 || dup2(nullfd, 0) < 0 || dup2(nullfd, 1) < 0 || dup2(nullfd, 2) < 0)
      _exit(127);
    if (nullfd > 2)
      close(nullfd);
    // Unprivileged callers may query this dedicated action without details.
    // Passing --detail program to the generic exec action is rejected by polkit.
    char *const args[] = {PKCHECK, "--action-id", ACTION, "--process", process, NULL};
    char *const environment[] = {"PATH=/usr/bin", "LANG=C", NULL};
    execve(PKCHECK, args, environment);
    _exit(127);
  }
  for (unsigned i = 0; i < 100; i++) {
    int status;
    pid_t result = waitpid(child, &status, WNOHANG);
    if (result == child)
      return WIFEXITED(status) && WEXITSTATUS(status) == 0;
    if (result < 0 && errno != EINTR)
      return false;
    struct timespec delay = {.tv_nsec = 10000000};
    nanosleep(&delay, NULL);
  }
  kill(child, SIGKILL);
  while (waitpid(child, NULL, 0) < 0 && errno == EINTR) {
  }
  return false;
}
static bool property(int fd, uint32_t object, uint32_t type, const char *name, uint64_t *value) {
  drmModeObjectPropertiesPtr props = drmModeObjectGetProperties(fd, object, type);
  if (!props)
    return false;
  bool found = false;
  for (uint32_t i = 0; i < props->count_props; i++) {
    drmModePropertyPtr p = drmModeGetProperty(fd, props->props[i]);
    if (p && !strcmp(p->name, name)) {
      *value = props->prop_values[i];
      found = true;
    }
    if (p)
      drmModeFreeProperty(p);
  }
  drmModeFreeObjectProperties(props);
  return found;
}
// Property IDs/enum meanings are immutable for a DRM object. Cache them once;
// each capture checks only current property values, never per-frame GetProperty.
enum field { FB, CRTC, TYPE, ROTATION, ALPHA, BLEND, SX, SY, SW, SH, FIELDS };
static const char *field_names[FIELDS] = {
    "FB_ID", "CRTC_ID", "type",  "rotation", "alpha", "pixel blend mode",
    "SRC_X", "SRC_Y",   "SRC_W", "SRC_H"};
struct plane_info {
  uint32_t id, properties[FIELDS];
  uint64_t blend_none, blend_premul, blend_coverage;
};
struct dpe_kms_inspector {
  int fd;
  struct dpe_kms_output output;
  uint32_t connector_crtc;
  unsigned count;
  struct plane_info planes[64];
};
struct dpe_kms_inspector *dpe_kms_inspector_create(int fd, const struct dpe_kms_output *output) {
  struct dpe_kms_inspector *inspector = calloc(1, sizeof(*inspector));
  if (!inspector)
    return NULL;
  inspector->fd = fd;
  inspector->output = *output;
  drmModeObjectPropertiesPtr connector =
      drmModeObjectGetProperties(fd, output->connector, DRM_MODE_OBJECT_CONNECTOR);
  for (uint32_t i = 0; connector && i < connector->count_props; i++) {
    drmModePropertyPtr p = drmModeGetProperty(fd, connector->props[i]);
    if (p && !strcmp(p->name, "CRTC_ID"))
      inspector->connector_crtc = p->prop_id;
    if (p)
      drmModeFreeProperty(p);
  }
  if (connector)
    drmModeFreeObjectProperties(connector);
  drmModePlaneResPtr planes = drmModeGetPlaneResources(fd);
  if (!planes || planes->count_planes > 64 || !inspector->connector_crtc)
    goto fail;
  for (uint32_t i = 0; i < planes->count_planes; i++) {
    struct plane_info *plane = &inspector->planes[inspector->count++];
    plane->id = planes->planes[i];
    plane->blend_none = plane->blend_premul = plane->blend_coverage = UINT64_MAX;
    drmModeObjectPropertiesPtr props =
        drmModeObjectGetProperties(fd, plane->id, DRM_MODE_OBJECT_PLANE);
    if (!props)
      goto fail;
    for (uint32_t j = 0; j < props->count_props; j++) {
      drmModePropertyPtr p = drmModeGetProperty(fd, props->props[j]);
      if (!p) {
        drmModeFreeObjectProperties(props);
        goto fail;
      }
      for (unsigned k = 0; k < FIELDS; k++)
        if (!strcmp(p->name, field_names[k]))
          plane->properties[k] = p->prop_id;
      if (!strcmp(p->name, "pixel blend mode"))
        for (int k = 0; k < p->count_enums; k++) {
          if (!strcmp(p->enums[k].name, "None"))
            plane->blend_none = p->enums[k].value;
          if (!strcmp(p->enums[k].name, "Pre-multiplied"))
            plane->blend_premul = p->enums[k].value;
          if (!strcmp(p->enums[k].name, "Coverage"))
            plane->blend_coverage = p->enums[k].value;
        }
      drmModeFreeProperty(p);
    }
    drmModeFreeObjectProperties(props);
    if (!plane->properties[FB] || !plane->properties[CRTC] || !plane->properties[TYPE])
      goto fail;
  }
  drmModeFreePlaneResources(planes);
  return inspector;
fail:
  if (planes)
    drmModeFreePlaneResources(planes);
  free(inspector);
  return NULL;
}
void dpe_kms_inspector_destroy(struct dpe_kms_inspector *inspector) { free(inspector); }
// GSR v6 omits reflection, fractional coordinates and plane blend information.
// Inspect them through unprivileged metadata and reject unsupported semantics.
bool dpe_kms_metadata(struct dpe_kms_inspector *inspector, struct dpe_kms_layout *layout) {
  if (!inspector)
    return false;
  const struct dpe_kms_output *output = &inspector->output;
  int fd = inspector->fd;
  *layout = (struct dpe_kms_layout){0};
  drmModeCrtcPtr crtc = drmModeGetCrtc(fd, output->crtc);
  bool valid =
      crtc && crtc->mode_valid && crtc->width == output->width && crtc->height == output->height;
  if (crtc)
    drmModeFreeCrtc(crtc);
  drmModeObjectPropertiesPtr connector =
      drmModeObjectGetProperties(fd, output->connector, DRM_MODE_OBJECT_CONNECTOR);
  bool connected = false;
  for (uint32_t i = 0; connector && i < connector->count_props; i++)
    if (connector->props[i] == inspector->connector_crtc)
      connected = connector->prop_values[i] == output->crtc;
  if (connector)
    drmModeFreeObjectProperties(connector);
  valid &= connected;
  unsigned primary = 0, cursor = 0;
  for (unsigned i = 0; i < inspector->count; i++) {
    const struct plane_info *plane = &inspector->planes[i];
    drmModeObjectPropertiesPtr props =
        drmModeObjectGetProperties(fd, plane->id, DRM_MODE_OBJECT_PLANE);
    if (!props) {
      valid = false;
      continue;
    }
    uint64_t values[FIELDS] = {0};
    bool have[FIELDS] = {0};
    for (uint32_t j = 0; j < props->count_props; j++)
      for (unsigned k = 0; k < FIELDS; k++)
        if (plane->properties[k] && plane->properties[k] == props->props[j]) {
          values[k] = props->prop_values[j];
          have[k] = true;
        }
    drmModeFreeObjectProperties(props);
    if (!have[FB] || !have[CRTC]) {
      valid = false;
      continue;
    }
    if (!values[FB] || values[CRTC] != output->crtc)
      continue;
    uint64_t type = values[TYPE];
    if (!have[TYPE])
      valid = false;
    if (type == DRM_PLANE_TYPE_PRIMARY)
      primary++;
    else if (type == DRM_PLANE_TYPE_CURSOR)
      cursor++;
    else
      valid = false;
    if ((have[ROTATION] && values[ROTATION] != DRM_MODE_ROTATE_0) ||
        (have[ALPHA] && values[ALPHA] != 65535))
      valid = false;
    for (unsigned k = SX; k <= SH; k++)
      if (!have[k] || (values[k] & 65535))
        valid = false;
    if (have[BLEND]) {
      if (type == DRM_PLANE_TYPE_CURSOR)
        valid &= values[BLEND] == plane->blend_premul;
      else if (values[BLEND] != plane->blend_none) {
        // The sole primary is composited over the KMS black background. Its
        // premultiplied RGB is already the correct opaque result, even with
        // alpha-bearing SDR formats. Coverage needs multiplication unless the
        // source format has no alpha; that case is checked with the response.
        if (values[BLEND] == plane->blend_coverage)
          layout->primary_needs_opaque = true;
        else if (values[BLEND] != plane->blend_premul)
          valid = false;
      }
    }
  }
  layout->planes = primary + cursor;
  layout->primary_present = primary != 0;
  layout->cursor_present = cursor != 0;
  // Missing planes during an atomic update are classified by scene() as a
  // retryable race. Multiple primaries/cursors or overlays are unsupported.
  return valid && primary <= 1 && cursor <= 1;
}
unsigned dpe_kms_inventory(struct dpe_kms_output outputs[DPE_KMS_OUTPUTS]) {
  drmDevicePtr devices[16] = {0};
  int count = drmGetDevices2(0, devices, 16);
  unsigned total = 0;
  for (int i = 0; i < count && total < DPE_KMS_OUTPUTS; i++) {
    drmDevicePtr device = devices[i];
    if (!(device->available_nodes & (1 << DRM_NODE_PRIMARY)) ||
        !(device->available_nodes & (1 << DRM_NODE_RENDER)))
      continue;
    int fd = open(device->nodes[DRM_NODE_PRIMARY], O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
      continue;
    bool atomic = drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) == 0 &&
                  drmSetClientCap(fd, DRM_CLIENT_CAP_ATOMIC, 1) == 0;
    drmModeResPtr resources = drmModeGetResources(fd);
    for (int j = 0; resources && j < resources->count_connectors && total < DPE_KMS_OUTPUTS; j++) {
      drmModeConnectorPtr c = drmModeGetConnectorCurrent(fd, resources->connectors[j]);
      if (!c)
        continue;
      uint64_t crtc_id = 0;
      property(fd, c->connector_id, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID", &crtc_id);
      drmModeCrtcPtr crtc = crtc_id ? drmModeGetCrtc(fd, crtc_id) : NULL;
      if (c->connection == DRM_MODE_CONNECTED && crtc && crtc->mode_valid && crtc->width &&
          crtc->height && crtc->width <= 8192 && crtc->height <= 8192) {
        struct dpe_kms_output *o = &outputs[total++];
        *o = (struct dpe_kms_output){.connector = c->connector_id,
                                     .crtc = crtc_id,
                                     .width = crtc->width,
                                     .height = crtc->height};
        const char *card = strrchr(device->nodes[DRM_NODE_PRIMARY], '/');
        snprintf(o->id, sizeof(o->id), "kms-%s-%u", card ? card + 1 : "card", c->connector_id);
        snprintf(o->name, sizeof(o->name), "%s-%u", drmModeGetConnectorTypeName(c->connector_type),
                 c->connector_type_id);
        snprintf(o->card, sizeof(o->card), "%s", device->nodes[DRM_NODE_PRIMARY]);
        snprintf(o->render, sizeof(o->render), "%s", device->nodes[DRM_NODE_RENDER]);
        struct dpe_kms_layout layout;
        struct dpe_kms_inspector *inspector = atomic ? dpe_kms_inspector_create(fd, o) : NULL;
        o->supported = inspector && dpe_kms_metadata(inspector, &layout) && layout.planes;
        dpe_kms_inspector_destroy(inspector);
      }
      if (crtc)
        drmModeFreeCrtc(crtc);
      drmModeFreeConnector(c);
    }
    if (resources)
      drmModeFreeResources(resources);
    close(fd);
  }
  if (count > 0)
    drmFreeDevices(devices, count);
  // The helper maps a cloned CRTC to only its first connector. Do not guess.
  for (unsigned i = 0; i < total; i++)
    for (unsigned j = 0; j < total; j++)
      if (i != j && !strcmp(outputs[i].card, outputs[j].card) && outputs[i].crtc == outputs[j].crtc)
        outputs[i].supported = false;
  return total;
}
static bool rgb(uint32_t format, bool cursor) {
  return format == DRM_FORMAT_ARGB8888 || format == DRM_FORMAT_ABGR8888 ||
         (!cursor && (format == DRM_FORMAT_XRGB8888 || format == DRM_FORMAT_XBGR8888 ||
                      format == DRM_FORMAT_ARGB2101010 || format == DRM_FORMAT_ABGR2101010 ||
                      format == DRM_FORMAT_XRGB2101010 || format == DRM_FORMAT_XBGR2101010));
}
enum dpe_kms_scene_result dpe_kms_scene(const gsr_kms_response *response,
                                        const struct dpe_kms_output *output,
                                        const struct dpe_kms_layout *layout,
                                        struct dpe_kms_scene *scene) {
  *scene = (struct dpe_kms_scene){0};
  unsigned count = 0;
  if (response->version != 6 || response->result || response->num_items < 0 ||
      response->num_items >= GSR_KMS_MAX_ITEMS)
    return DPE_KMS_UNSUPPORTED; // A full response may have silently truncated active planes.
  for (int i = 0; i < response->num_items; i++) {
    const gsr_kms_response_item *p = &response->items[i];
    if (!p->connector_id)
      return DPE_KMS_UNSUPPORTED;
    if (p->connector_id != output->connector)
      continue;
    bool cursor = p->plane_type == KMS_PLANE_TYPE_CURSOR;
    if ((!cursor && p->plane_type != KMS_PLANE_TYPE_PRIMARY) || p->rotation != KMS_ROT_0 ||
        p->num_dma_bufs < 1 || p->num_dma_bufs > 4 || !rgb(p->pixel_format, cursor) || !p->width ||
        !p->height || p->width > 16384 || p->height > 16384 || p->src_x < 0 || p->src_y < 0 ||
        p->src_w <= 0 || p->src_h <= 0 || (uint64_t)p->src_x + p->src_w > p->width ||
        (uint64_t)p->src_y + p->src_h > p->height || p->dst_w <= 0 || p->dst_h <= 0 ||
        p->dst_w > 16384 || p->dst_h > 16384 || p->dst_x < -16384 || p->dst_y < -16384 ||
        p->dst_x > 16384 || p->dst_y > 16384 ||
        (p->has_hdr_metadata &&
         (p->hdr_metadata.metadata_type != 0 || p->hdr_metadata.hdmi_metadata_type1.eotf != 0)))
      return DPE_KMS_UNSUPPORTED;
    for (int j = 0; j < p->num_dma_bufs; j++)
      if (p->dma_buf[j].fd < 0 || !p->dma_buf[j].pitch || p->dma_buf[j].pitch > 262144 ||
          p->dma_buf[j].offset > INT32_MAX)
        return DPE_KMS_UNSUPPORTED;
    if (cursor) {
      if (scene->cursor)
        return DPE_KMS_UNSUPPORTED;
      scene->cursor = p;
    } else {
      if (scene->primary)
        return DPE_KMS_UNSUPPORTED;
      scene->primary = p;
    }
    count++;
  }
  const gsr_kms_response_item *primary = scene->primary;
  if (count != layout->planes || !primary || !layout->primary_present ||
      (scene->cursor != NULL) != layout->cursor_present)
    return DPE_KMS_CHANGED;
  if (layout->primary_needs_opaque && primary->pixel_format != DRM_FORMAT_XRGB8888 &&
      primary->pixel_format != DRM_FORMAT_XBGR8888 &&
      primary->pixel_format != DRM_FORMAT_XRGB2101010 &&
      primary->pixel_format != DRM_FORMAT_XBGR2101010)
    return DPE_KMS_UNSUPPORTED;
  return !primary->dst_x && !primary->dst_y && (uint32_t)primary->dst_w == output->width &&
                 (uint32_t)primary->dst_h == output->height &&
                 (!scene->cursor || scene->cursor->zpos > primary->zpos)
             ? DPE_KMS_VALID
             : DPE_KMS_UNSUPPORTED;
}
void dpe_kms_response_close(gsr_kms_response *response) {
  for (int i = 0; i < response->num_items && i < GSR_KMS_MAX_ITEMS; i++)
    for (int j = 0; j < response->items[i].num_dma_bufs && j < GSR_KMS_MAX_DMA_BUFS; j++) {
      if (response->items[i].dma_buf[j].fd >= 0)
        close(response->items[i].dma_buf[j].fd);
      response->items[i].dma_buf[j].fd = -1;
    }
  response->num_items = 0;
}
int dpe_kms_receive(int fd, gsr_kms_response *response) {
  memset(response, 0, sizeof(*response));
  unsigned char control[CMSG_SPACE(sizeof(int) * GSR_KMS_MAX_ITEMS * GSR_KMS_MAX_DMA_BUFS)] = {0};
  struct iovec vector = {.iov_base = response, .iov_len = sizeof(*response)};
  struct msghdr message = {.msg_iov = &vector,
                           .msg_iovlen = 1,
                           .msg_control = control,
                           .msg_controllen = sizeof(control)};
  int fds[32], nfd = 0;
  ssize_t size = recvmsg(fd, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
  if (size < 0 && (errno == EAGAIN || errno == EINTR))
    return 0;
  bool valid = size == sizeof(*response) && !(message.msg_flags & (MSG_TRUNC | MSG_CTRUNC));
  for (struct cmsghdr *c = CMSG_FIRSTHDR(&message); c; c = CMSG_NXTHDR(&message, c)) {
    if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS || c->cmsg_len < CMSG_LEN(0)) {
      valid = false;
      continue;
    }
    size_t bytes = c->cmsg_len - CMSG_LEN(0);
    if (bytes % sizeof(int))
      valid = false;
    for (size_t i = 0; i < bytes / sizeof(int); i++) {
      int received;
      memcpy(&received, (char *)CMSG_DATA(c) + i * sizeof(int), sizeof(int));
      if (nfd < 32)
        fds[nfd++] = received;
      else {
        close(received);
        valid = false;
      }
    }
  }
  valid &= response->version == 6 && response->num_items >= 0 && response->num_items <= 8;
  unsigned expected = 0;
  for (int i = 0; valid && i < response->num_items; i++) {
    int planes = response->items[i].num_dma_bufs;
    if (planes < 1 || planes > 4)
      valid = false;
    else
      expected += planes;
  }
  valid &= expected == (unsigned)nfd;
  bool permission_denied =
      valid && response->result == KMS_RESULT_FAILED_TO_GET_PLANE &&
      !strncmp(response->err_msg, "drmfb handle is NULL", sizeof(response->err_msg));
  if (!valid || response->result) {
    for (int i = 0; i < nfd; i++)
      close(fds[i]);
    memset(response, 0, sizeof(*response));
    return permission_denied ? DPE_KMS_PERMISSION_DENIED : -1;
  }
  unsigned at = 0;
  for (int i = 0; i < response->num_items; i++)
    for (int j = 0; j < response->items[i].num_dma_bufs; j++)
      response->items[i].dma_buf[j].fd = fds[at++];
  return 1;
}
bool dpe_kms_request(struct dpe_kms_client *client) {
  gsr_kms_request request = {
      .version = 6, .type = KMS_REQUEST_TYPE_GET_KMS, .new_connection_fd = -1};
  return send(client->fd, &request, sizeof(request), MSG_NOSIGNAL | MSG_DONTWAIT) ==
         sizeof(request);
}
void dpe_kms_client_close(struct dpe_kms_client *client) {
  if (client->fd >= 0)
    close(client->fd);
  client->fd = -1;
  if (client->pid > 0) {
    // Only our unreaped child PID is retained. An authorized root child may
    // reject signals, but closing its socket makes GSR exit. Never block the
    // source indefinitely waiting for a privileged child to exit.
    kill(client->pid, SIGKILL);
    for (unsigned i = 0; i < 20; i++) {
      pid_t result = waitpid(client->pid, NULL, WNOHANG);
      if (result == client->pid || (result < 0 && errno != EINTR))
        break;
      struct timespec delay = {.tv_nsec = 10000000};
      nanosleep(&delay, NULL);
    }
  }
  client->pid = -1;
}
static volatile sig_atomic_t opening_cancelled;
static void cancel_open(int signal) {
  (void)signal;
  opening_cancelled = 1;
}
static uint64_t monotonic_ms(void) {
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  return (uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}
static bool await_helper(struct dpe_kms_client *client, int listener) {
  uint64_t deadline = monotonic_ms() + 1500;
  while (!opening_cancelled && monotonic_ms() < deadline) {
    struct pollfd wait = {.fd = listener, .events = POLLIN};
    int polled = poll(&wait, 1, 100);
    if (polled > 0)
      return (wait.revents & POLLIN) != 0;
    if (polled < 0 && errno != EINTR)
      break;
    int status;
    pid_t reaped = waitpid(client->pid, &status, WNOHANG);
    if (reaped == client->pid) {
      client->pid = -1;
      if (WIFEXITED(status) && (WEXITSTATUS(status) == 126 || WEXITSTATUS(status) == 127))
        client->error = "permission-required";
      return false;
    }
  }
  if (opening_cancelled)
    client->error = "authorization-cancelled";
  return false;
}
bool dpe_kms_client_open(struct dpe_kms_client *client, const char *card, bool authorize) {
  (void)authorize; // Legacy caller ABI; permission changes belong to Settings.
  *client = (struct dpe_kms_client){.fd = -1, .pid = -1, .error = "unsupported"};
  const char *status = dpe_kms_helper_status();
  if (status) {
    client->error = status;
    return false;
  }
  if (!scoped_permission() || !scoped_authorized()) {
    client->error = "permission-required";
    return false;
  }
  char directory[] = "/tmp/dpe-kms-XXXXXX";
  if (!mkdtemp(directory))
    return false;
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  snprintf(address.sun_path, sizeof(address.sun_path), "%s/socket", directory);
  int listener = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0), accepted = -1, pair[2] = {-1, -1};
  bool success = false;
  struct sigaction action = {.sa_handler = cancel_open}, old_term, old_int;
  sigemptyset(&action.sa_mask);
  opening_cancelled = 0;
  sigaction(SIGTERM, &action, &old_term);
  sigaction(SIGINT, &action, &old_int);
  if (listener < 0 || bind(listener, (void *)&address, sizeof(address)) || listen(listener, 1))
    goto done;
  client->pid = fork();
  if (client->pid < 0)
    goto done;
  if (!client->pid) {
    close(listener);
    // Only the unprivileged producer owns the JSON/control pipes. In
    // particular, a pending polkit prompt must not hold its stdout open after
    // the producer exits and delay the controller's terminal error handling.
    int null_input = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (null_input < 0 || dup2(null_input, STDIN_FILENO) < 0 ||
        dup2(STDERR_FILENO, STDOUT_FILENO) < 0)
      _exit(127);
    if (null_input != STDIN_FILENO)
      close(null_input);
    char *const authorized_args[] = {
        PKEXEC, "--disable-internal-agent", SCOPED_HELPER, address.sun_path, (char *)card, NULL};
    char *const environment[] = {"PATH=/usr/bin", "LANG=C", NULL};
    execve(PKEXEC, authorized_args, environment);
    _exit(127);
  }
  if (!await_helper(client, listener))
    goto done;
  accepted = accept4(listener, NULL, NULL, SOCK_CLOEXEC);
  struct ucred peer;
  socklen_t length = sizeof(peer);
  if (accepted < 0 || getsockopt(accepted, SOL_SOCKET, SO_PEERCRED, &peer, &length) ||
      peer.pid != client->pid || peer.uid != 0 ||
      socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair))
    goto done;
  cap_t running_caps = cap_get_pid(client->pid);
  cap_flag_value_t admin = CAP_CLEAR;
  if (running_caps) {
    cap_get_flag(running_caps, CAP_SYS_ADMIN, CAP_EFFECTIVE, &admin);
    cap_free(running_caps);
  }
  fprintf(stderr, "DPE KMS: launcher no-new-privileges=%d helper-admin=%d\n",
          prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0), admin == CAP_SET);
  gsr_kms_request request = {
      .version = 6, .type = KMS_REQUEST_TYPE_REPLACE_CONNECTION, .new_connection_fd = pair[1]};
  struct iovec vector = {.iov_base = &request, .iov_len = sizeof(request)};
  unsigned char control[CMSG_SPACE(sizeof(int))] = {0};
  struct msghdr message = {.msg_iov = &vector,
                           .msg_iovlen = 1,
                           .msg_control = control,
                           .msg_controllen = sizeof(control)};
  struct cmsghdr *c = CMSG_FIRSTHDR(&message);
  c->cmsg_level = SOL_SOCKET;
  c->cmsg_type = SCM_RIGHTS;
  c->cmsg_len = CMSG_LEN(sizeof(int));
  memcpy(CMSG_DATA(c), &pair[1], sizeof(int));
  if (sendmsg(accepted, &message, MSG_NOSIGNAL) != sizeof(request))
    goto done;
  close(pair[1]);
  pair[1] = -1;
  struct pollfd wait = {.fd = pair[0], .events = POLLIN};
  gsr_kms_response response = {0};
  if (poll(&wait, 1, 500) != 1 || !(wait.revents & POLLIN) ||
      dpe_kms_receive(pair[0], &response) != 1)
    goto done;
  if (response.num_items) {
    dpe_kms_response_close(&response);
    goto done;
  }
  client->fd = pair[0];
  pair[0] = -1;
  // The socket handshake alone says nothing about GETFB2 access. Check it
  // after explicit source selection, before publishing a ready PipeWire node.
  // Descriptors are closed immediately; no pixels are mapped or saved.
  wait = (struct pollfd){.fd = client->fd, .events = POLLIN};
  if (!dpe_kms_request(client) || poll(&wait, 1, 500) != 1 || !(wait.revents & POLLIN))
    goto done;
  int checked = dpe_kms_receive(client->fd, &response);
  if (checked != 1) {
    if (checked == DPE_KMS_PERMISSION_DENIED)
      client->error = "permission-blocked";
    goto done;
  }
  dpe_kms_response_close(&response);
  client->error = NULL;
  success = !opening_cancelled;
done:
  if (accepted >= 0)
    close(accepted);
  if (listener >= 0)
    close(listener);
  for (int i = 0; i < 2; i++)
    if (pair[i] >= 0)
      close(pair[i]);
  unlink(address.sun_path);
  rmdir(directory);
  if (!success)
    dpe_kms_client_close(client);
  sigaction(SIGTERM, &old_term, NULL);
  sigaction(SIGINT, &old_int, NULL);
  return success;
}
