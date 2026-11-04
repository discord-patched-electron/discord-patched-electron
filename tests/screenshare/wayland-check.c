// Real libwayland client/server wire protocol over a socketpair. The private
// server writes only its synthetic SHM buffers. No host display/GPU is opened.
#define _GNU_SOURCE
#include "../../integration/video/wayland-capture.h"
#include "ext-image-capture-source-v1-server.h"
#include "ext-image-copy-capture-v1-server.h"
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include <wayland-server.h>

struct server {
  struct wl_display *display;
  struct wl_resource *session, *frame, *buffer;
  struct wl_global *selected;
  char mode;
  unsigned captures;
  int control;
  bool damaged;
};
static void destroy_request(struct wl_client *client, struct wl_resource *resource) {
  (void)client;
  wl_resource_destroy(resource);
}
static const struct wl_output_interface output_impl = {.release = destroy_request};
static void bind_output(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
  struct wl_resource *output = wl_resource_create(client, &wl_output_interface, version, id);
  wl_resource_set_implementation(output, &output_impl, data, NULL);
  wl_output_send_name(output, data);
  wl_output_send_done(output);
}
static void release_frame(struct wl_resource *frame) {
  struct server *server = wl_resource_get_user_data(frame);
  server->frame = NULL;
  server->buffer = NULL;
}
static void attach(struct wl_client *client, struct wl_resource *frame,
                   struct wl_resource *buffer) {
  (void)client;
  struct server *server = wl_resource_get_user_data(frame);
  server->buffer = buffer;
}
static void damage(struct wl_client *client, struct wl_resource *frame, int32_t x, int32_t y,
                   int32_t width, int32_t height) {
  (void)client;
  struct server *server = wl_resource_get_user_data(frame);
  struct wl_shm_buffer *buffer = wl_shm_buffer_get(server->buffer);
  assert(buffer && x == 0 && y == 0 && width == wl_shm_buffer_get_width(buffer) &&
         height == wl_shm_buffer_get_height(buffer));
  server->damaged = true;
}
static void complete(struct server *server) {
  assert(server->frame && server->buffer && server->damaged);
  struct wl_shm_buffer *buffer = wl_shm_buffer_get(server->buffer);
  int32_t width = wl_shm_buffer_get_width(buffer), height = wl_shm_buffer_get_height(buffer);
  wl_shm_buffer_begin_access(buffer);
  uint32_t *pixels = wl_shm_buffer_get_data(buffer);
  for (int i = 0; i < width * height; i++)
    pixels[i] = 0xff000000u | server->captures;
  wl_shm_buffer_end_access(buffer);
  ext_image_copy_capture_frame_v1_send_damage(server->frame, 0, 0, width, height);
  ext_image_copy_capture_frame_v1_send_transform(server->frame, WL_OUTPUT_TRANSFORM_90);
  if (server->mode != 'T')
    ext_image_copy_capture_frame_v1_send_presentation_time(server->frame, 0, 123,
                                                           server->mode == 'N' ? 1000000000 : 456);
  ext_image_copy_capture_frame_v1_send_ready(server->frame);
}
static void capture_request(struct wl_client *client, struct wl_resource *frame) {
  (void)client;
  struct server *server = wl_resource_get_user_data(frame);
  server->captures++;
  assert(server->damaged && server->buffer);
  if (server->mode == 'H')
    return;
  if (server->mode == 'F')
    ext_image_copy_capture_frame_v1_send_failed(
        frame, EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_BUFFER_CONSTRAINTS);
  else if (server->mode == 'U')
    ext_image_copy_capture_frame_v1_send_failed(
        frame, EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_UNKNOWN);
  else
    complete(server);
}
static const struct ext_image_copy_capture_frame_v1_interface frame_impl = {
    destroy_request, attach, damage, capture_request};
static void create_frame(struct wl_client *client, struct wl_resource *session, uint32_t id) {
  struct server *server = wl_resource_get_user_data(session);
  assert(!server->frame); // The client must destroy a frame before creating another.
  server->frame = wl_resource_create(client, &ext_image_copy_capture_frame_v1_interface, 1, id);
  server->damaged = false;
  wl_resource_set_implementation(server->frame, &frame_impl, server, release_frame);
}
static const struct ext_image_copy_capture_session_v1_interface session_impl = {create_frame,
                                                                                destroy_request};
static void constraints(struct server *server, uint32_t size) {
  ext_image_copy_capture_session_v1_send_shm_format(server->session, WL_SHM_FORMAT_ARGB8888);
  uint64_t modifiers[] = {0, 0x0100000000000001ull};
  struct wl_array array = {sizeof(modifiers), sizeof(modifiers), modifiers};
  if (server->mode == 'G')
    for (unsigned i = 0; i < 300; i++)
      ext_image_copy_capture_session_v1_send_dmabuf_format(server->session, 0x10000000 + i, &array);
  ext_image_copy_capture_session_v1_send_dmabuf_format(server->session, 0x34325241, &array);
  ext_image_copy_capture_session_v1_send_buffer_size(server->session, size, size);
  ext_image_copy_capture_session_v1_send_done(server->session);
}
static void create_session(struct wl_client *client, struct wl_resource *manager, uint32_t id,
                           struct wl_resource *source, uint32_t options) {
  struct server *server = wl_resource_get_user_data(manager);
  assert(wl_resource_get_user_data(source) == server && options == 1);
  server->session = wl_resource_create(client, &ext_image_copy_capture_session_v1_interface, 1, id);
  wl_resource_set_implementation(server->session, &session_impl, server, NULL);
  constraints(server, 16);
}
static const struct ext_image_copy_capture_manager_v1_interface manager_impl = {
    .create_session = create_session, .destroy = destroy_request};
static void bind_manager(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
  struct wl_resource *manager =
      wl_resource_create(client, &ext_image_copy_capture_manager_v1_interface, version, id);
  wl_resource_set_implementation(manager, &manager_impl, data, NULL);
}
static const struct ext_image_capture_source_v1_interface source_impl = {destroy_request};
static void create_source(struct wl_client *client, struct wl_resource *manager, uint32_t id,
                          struct wl_resource *output) {
  assert(!strcmp(wl_resource_get_user_data(output), "DPE-1"));
  struct wl_resource *source =
      wl_resource_create(client, &ext_image_capture_source_v1_interface, 1, id);
  wl_resource_set_implementation(source, &source_impl, wl_resource_get_user_data(manager), NULL);
}
static const struct ext_output_image_capture_source_manager_v1_interface sources_impl = {
    create_source, destroy_request};
static void bind_sources(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
  struct wl_resource *sources = wl_resource_create(
      client, &ext_output_image_capture_source_manager_v1_interface, version, id);
  wl_resource_set_implementation(sources, &sources_impl, data, NULL);
}
static int control(int fd, uint32_t mask, void *data) {
  (void)mask;
  struct server *server = data;
  char command;
  if (read(fd, &command, 1) != 1) {
    wl_display_terminate(server->display);
    return 0;
  }
  if (command == 'R')
    complete(server);
  else if (command == 'C')
    constraints(server, 8);
  else if (command == 'P') {
    ext_image_copy_capture_session_v1_send_shm_format(server->session, WL_SHM_FORMAT_ARGB8888);
    ext_image_copy_capture_session_v1_send_buffer_size(server->session, 16, 16);
  } else if (command == 'Q')
    ext_image_copy_capture_session_v1_send_done(server->session);
  else if (command == 'S')
    ext_image_copy_capture_session_v1_send_stopped(server->session);
  else if (command == 'D')
    wl_global_remove(server->selected);
  else if (command == 'B') {
    uint8_t bytes[3] = {0};
    struct wl_array array = {sizeof(bytes), sizeof(bytes), bytes};
    ext_image_copy_capture_session_v1_send_dmabuf_format(server->session, 0x34325241, &array);
    ext_image_copy_capture_session_v1_send_done(server->session);
  } else
    server->mode = command;
  wl_display_flush_clients(server->display);
  assert(write(fd, &command, 1) == 1);
  return 0;
}
static void server_run(int socket, int commands) {
  assert(prctl(PR_SET_PDEATHSIG, SIGKILL) == 0);
  struct server server = {.display = wl_display_create(), .mode = 'A', .control = commands};
  assert(server.display && wl_display_init_shm(server.display) == 0);
  // Deliberately advertise the other monitor first. Selection must use its name.
  assert(wl_global_create(server.display, &wl_output_interface, 4, "DPE-2", bind_output));
  server.selected = wl_global_create(server.display, &wl_output_interface, 4, "DPE-1", bind_output);
  assert(server.selected);
  assert(wl_global_create(server.display, &ext_output_image_capture_source_manager_v1_interface, 1,
                          &server, bind_sources));
  assert(wl_global_create(server.display, &ext_image_copy_capture_manager_v1_interface, 1, &server,
                          bind_manager));
  assert(wl_client_create(server.display, socket));
  struct wl_event_source *source = wl_event_loop_add_fd(
      wl_display_get_event_loop(server.display), commands, WL_EVENT_READABLE, control, &server);
  wl_display_run(server.display);
  wl_event_source_remove(source);
  wl_display_destroy_clients(server.display);
  wl_display_destroy(server.display);
  close(commands);
  exit(0);
}

struct fixture {
  struct wl_display *display;
  struct wl_registry *registry;
  struct wl_shm *shm;
  struct dpe_wl_capture *capture;
  struct dpe_wl_constraints constraints;
  struct dpe_wl_frame frame;
  int frames, changes, stopped, status, control;
  pid_t child;
};
static void got_constraints(void *data, const struct dpe_wl_constraints *value) {
  struct fixture *fixture = data;
  fixture->constraints = *value;
  fixture->changes++;
}
static void got_frame(void *data, const struct dpe_wl_buffer *buffer,
                      const struct dpe_wl_frame *value, int status) {
  struct fixture *fixture = data;
  assert(buffer->generation == value->generation);
  fixture->frame = *value;
  fixture->status = status;
  fixture->frames++;
}
static void got_stop(void *data, int reason) { ((struct fixture *)data)->stopped = reason; }
static const struct dpe_wl_callbacks callbacks = {got_constraints, got_frame, got_stop};
static void registry_global(void *data, struct wl_registry *registry, uint32_t name,
                            const char *interface, uint32_t version) {
  (void)version;
  struct fixture *fixture = data;
  if (!strcmp(interface, wl_shm_interface.name))
    fixture->shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
}
static void registry_remove(void *data, struct wl_registry *registry, uint32_t name) {
  (void)data;
  (void)registry;
  (void)name;
}
static const struct wl_registry_listener registry_listener = {registry_global, registry_remove};
static void roundtrip(struct fixture *fixture) {
  assert(wl_display_roundtrip(fixture->display) >= 0);
}
static void command(struct fixture *fixture, char code) {
  assert(write(fixture->control, &code, 1) == 1);
  char reply;
  assert(read(fixture->control, &reply, 1) == 1 && reply == code);
  roundtrip(fixture);
}
static void setup(struct fixture *fixture, const char *name) {
  *fixture = (struct fixture){0};
  int wire[2], commands[2];
  assert(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, wire) == 0);
  assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, commands) == 0);
  fixture->child = fork();
  assert(fixture->child >= 0);
  if (!fixture->child) {
    close(wire[0]);
    close(commands[0]);
    server_run(wire[1], commands[1]);
  }
  close(wire[1]);
  close(commands[1]);
  fixture->control = commands[0];
  fixture->display = wl_display_connect_to_fd(wire[0]);
  assert(fixture->display);
  fixture->registry = wl_display_get_registry(fixture->display);
  wl_registry_add_listener(fixture->registry, &registry_listener, fixture);
  fixture->capture = dpe_wl_capture_new(fixture->display, name, &callbacks, fixture);
  assert(fixture->capture);
  roundtrip(fixture);
  roundtrip(fixture);
}
static void teardown(struct fixture *fixture) {
  dpe_wl_capture_destroy(fixture->capture);
  wl_shm_destroy(fixture->shm);
  wl_registry_destroy(fixture->registry);
  roundtrip(fixture);
  wl_display_disconnect(fixture->display);
  close(fixture->control);
  int status;
  assert(waitpid(fixture->child, &status, 0) == fixture->child && WIFEXITED(status) &&
         WEXITSTATUS(status) == 0);
}
struct buffer {
  struct dpe_wl_buffer descriptor;
  uint32_t *pixels;
  size_t bytes;
};
static struct buffer allocate(struct fixture *fixture) {
  unsigned size = fixture->constraints.width;
  struct buffer buffer = {.bytes = size * size * 4};
  int fd = memfd_create("dpe-private-protocol-test", MFD_CLOEXEC);
  assert(fd >= 0 && ftruncate(fd, buffer.bytes) == 0);
  buffer.pixels = mmap(NULL, buffer.bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  assert(buffer.pixels != MAP_FAILED);
  struct wl_shm_pool *pool = wl_shm_create_pool(fixture->shm, fd, buffer.bytes);
  buffer.descriptor = (struct dpe_wl_buffer){
      .handle = wl_shm_pool_create_buffer(pool, 0, size, size, size * 4, WL_SHM_FORMAT_ARGB8888),
      .generation = fixture->constraints.generation,
      .width = size,
      .height = size,
      .format = {.format = WL_SHM_FORMAT_ARGB8888}};
  wl_shm_pool_destroy(pool);
  close(fd);
  return buffer;
}
static void destroy_buffer(struct buffer *buffer) {
  wl_buffer_destroy(buffer->descriptor.handle);
  munmap(buffer->pixels, buffer->bytes);
}
static bool shm_only(void *data, uint32_t format, bool dmabuf) {
  (void)data;
  return !dmabuf && format == WL_SHM_FORMAT_ARGB8888;
}
int main(void) {
  struct fixture fixture;
  setup(&fixture, "missing-monitor");
  assert(dpe_wl_capture_start(fixture.capture, true) == -ENOENT);
  teardown(&fixture);

  // A compositor runtime error does not end the protocol session. A later
  // paced frame can reuse the valid buffer and succeed without re-selection.
  setup(&fixture, "DPE-1");
  assert(dpe_wl_capture_start(fixture.capture, true) == 0);
  roundtrip(&fixture);
  struct buffer retry = allocate(&fixture);
  command(&fixture, 'U');
  assert(dpe_wl_capture_submit(fixture.capture, &retry.descriptor) == 0);
  roundtrip(&fixture);
  assert(fixture.frames == 1 && fixture.status == -EAGAIN && !fixture.stopped);
  command(&fixture, 'O');
  assert(dpe_wl_capture_submit(fixture.capture, &retry.descriptor) == 0);
  roundtrip(&fixture);
  assert(fixture.frames == 2 && fixture.status == 0 && !fixture.stopped);
  // Constraint batches may span socket reads and timer callbacks.
  command(&fixture, 'P');
  assert(dpe_wl_capture_submit(fixture.capture, &retry.descriptor) == -EAGAIN);
  assert(!fixture.stopped);
  command(&fixture, 'Q');
  retry.descriptor.generation = fixture.constraints.generation;
  assert(dpe_wl_capture_submit(fixture.capture, &retry.descriptor) == 0);
  roundtrip(&fixture);
  assert(fixture.frames == 3 && fixture.status == 0 && !fixture.stopped);
  destroy_buffer(&retry);
  teardown(&fixture);

  setup(&fixture, "DPE-1");
  command(&fixture, 'G');
  assert(dpe_wl_capture_set_format_filter(fixture.capture, shm_only) == 0);
  assert(dpe_wl_capture_start(fixture.capture, true) == 0);
  assert(dpe_wl_capture_set_format_filter(fixture.capture, NULL) == -EINVAL);
  roundtrip(&fixture);
  assert(fixture.changes == 1 && fixture.constraints.format_count == 1 && !fixture.stopped);
  teardown(&fixture);

  setup(&fixture, "DPE-1");
  assert(dpe_wl_capture_start(fixture.capture, true) == 0);
  roundtrip(&fixture);
  assert(fixture.constraints.format_count == 3 && fixture.changes == 1);
  struct buffer first = allocate(&fixture);
  assert(dpe_wl_capture_submit(fixture.capture, &first.descriptor) == 0);
  assert(dpe_wl_capture_submit(fixture.capture, &first.descriptor) == -EBUSY);
  roundtrip(&fixture);
  assert(fixture.frames == 1 && fixture.status == 0 && first.pixels[0] == 0xff000001);
  assert(fixture.frame.presentation_ns == 123000000456ull &&
         fixture.frame.transform == WL_OUTPUT_TRANSFORM_90);
  assert(fixture.frame.width == 16 && fixture.frame.height == 16);
  command(&fixture, 'H');
  assert(dpe_wl_capture_submit(fixture.capture, &first.descriptor) == 0);
  roundtrip(&fixture);
  command(&fixture, 'C');
  assert(fixture.changes == 2 && fixture.frames == 1 && fixture.constraints.generation == 2);
  command(&fixture, 'R');
  assert(fixture.frame.generation == 1 && fixture.frames == 2);
  assert(dpe_wl_capture_submit(fixture.capture, &first.descriptor) == -ESTALE);
  struct buffer next = allocate(&fixture);
  command(&fixture, 'F');
  assert(dpe_wl_capture_submit(fixture.capture, &next.descriptor) == 0);
  roundtrip(&fixture);
  assert(fixture.frames == 3 && fixture.status == -ESTALE && !fixture.stopped);
  assert(dpe_wl_capture_submit(fixture.capture, &next.descriptor) == -ESTALE);
  command(&fixture, 'C');
  next.descriptor.generation = fixture.constraints.generation;
  command(&fixture, 'H');
  assert(dpe_wl_capture_submit(fixture.capture, &next.descriptor) == 0);
  roundtrip(&fixture);
  command(&fixture, 'S');
  assert(fixture.stopped == -ECANCELED && fixture.frames == 3);
  assert(dpe_wl_capture_submit(fixture.capture, &next.descriptor) == -ECANCELED);
  command(&fixture, 'R');
  assert(fixture.frames == 4 && fixture.status == -ECANCELED);
  destroy_buffer(&first);
  destroy_buffer(&next);
  teardown(&fixture);

  // Disconnecting an idle/static source must not invent a successful frame or
  // release its borrowed storage while the compositor might still write to it.
  setup(&fixture, "DPE-1");
  assert(dpe_wl_capture_start(fixture.capture, true) == 0);
  roundtrip(&fixture);
  struct buffer cancelled = allocate(&fixture);
  command(&fixture, 'H');
  assert(dpe_wl_capture_submit(fixture.capture, &cancelled.descriptor) == 0);
  roundtrip(&fixture);
  assert(fixture.frames == 0);
  dpe_wl_capture_destroy(fixture.capture);
  fixture.capture = NULL;
  wl_buffer_destroy(cancelled.descriptor.handle);
  teardown(&fixture);
  assert(fixture.frames == 0);
  munmap(cancelled.pixels, cancelled.bytes);

  for (const char *mode = "NTBD"; *mode; mode++) {
    setup(&fixture, "DPE-1");
    assert(dpe_wl_capture_start(fixture.capture, true) == 0);
    roundtrip(&fixture);
    struct buffer buffer = allocate(&fixture);
    command(&fixture, *mode);
    if (*mode == 'N' || *mode == 'T') {
      assert(dpe_wl_capture_submit(fixture.capture, &buffer.descriptor) == 0);
      roundtrip(&fixture);
      assert(fixture.frames == 1 && fixture.status == -EPROTO);
    }
    assert(fixture.stopped == (*mode == 'D' ? -ENODEV : -EPROTO));
    assert(dpe_wl_capture_submit(fixture.capture, &buffer.descriptor) == fixture.stopped);
    destroy_buffer(&buffer);
    teardown(&fixture);
  }
  puts("Wayland capture: exact output, SHM delivery, bounded requests, generations, transforms, "
       "timestamps, cancellation and malformed events passed");
  return 0;
}
