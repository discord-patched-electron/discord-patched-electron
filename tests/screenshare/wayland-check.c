// Real libwayland client/server wire protocol over a socketpair. The private
// server writes only its synthetic SHM buffers. No host display/GPU is opened.
#define _GNU_SOURCE
#include "ext-image-capture-source-v1-server.h"
#include "ext-image-copy-capture-v1-server.h"
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
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
  struct wl_global *selected, *other;
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
  else if (command == '+')
    assert(wl_global_create(server->display, &wl_output_interface, 4, "DPE-3", bind_output));
  else if (command == '-')
    wl_global_remove(server->other);
  else if (command == 'L')
    assert(wl_global_create(server->display, &wl_output_interface, 4, "DPE-1", bind_output));
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
  server.other = wl_global_create(server.display, &wl_output_interface, 4, "DPE-2", bind_output);
  assert(server.other);
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

int main(int argc, char **argv) {
  assert(argc == 3);
  server_run(atoi(argv[1]), atoi(argv[2]));
  return 0;
}
