// SPDX-License-Identifier: MIT
// Private compositor exercising the real cursor-only wire path, never host content.
#define _GNU_SOURCE
#include "../../integration/video/vkcapture-wayland-cursor.h"
#include "ext-foreign-toplevel-list-v1-server.h"
#include "ext-image-capture-source-v1-server.h"
#include "ext-image-copy-capture-v1-server.h"
#include <assert.h>
#include <signal.h>
#include <spa/buffer/meta.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <wayland-server.h>

static struct wl_resource *wire_buffer, *wire_cursor_session;
static unsigned wire_captures;
static int wire_proceed;
static void wire_destroy(struct wl_client *client, struct wl_resource *r) {
  (void)client;
  wl_resource_destroy(r);
}
static const struct ext_foreign_toplevel_handle_v1_interface wire_top_impl = {wire_destroy};
static const struct ext_foreign_toplevel_list_v1_interface wire_list_impl = {wire_destroy,
                                                                             wire_destroy};
static void wire_list(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
  (void)data;
  struct wl_resource *r =
      wl_resource_create(client, &ext_foreign_toplevel_list_v1_interface, version, id);
  wl_resource_set_implementation(r, &wire_list_impl, NULL, NULL);
  struct wl_resource *top =
      wl_resource_create(client, &ext_foreign_toplevel_handle_v1_interface, 1, 0);
  wl_resource_set_implementation(top, &wire_top_impl, NULL, NULL);
  ext_foreign_toplevel_list_v1_send_toplevel(r, top);
  ext_foreign_toplevel_handle_v1_send_identifier(top, "synthetic-game");
  ext_foreign_toplevel_handle_v1_send_done(top);
}
static void wire_pointer(struct wl_client *client, struct wl_resource *seat, uint32_t id) {
  (void)seat;
  struct wl_resource *r = wl_resource_create(client, &wl_pointer_interface, 1, id);
  wl_resource_set_implementation(r, NULL, NULL, NULL);
}
static const struct wl_seat_interface wire_seat_impl = {.get_pointer = wire_pointer};
static void wire_seat(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
  (void)data;
  struct wl_resource *r = wl_resource_create(client, &wl_seat_interface, version, id);
  wl_resource_set_implementation(r, &wire_seat_impl, NULL, NULL);
  wl_seat_send_capabilities(r, WL_SEAT_CAPABILITY_POINTER);
}
static const struct ext_image_capture_source_v1_interface wire_source_impl = {wire_destroy};
static void wire_source(struct wl_client *client, struct wl_resource *manager, uint32_t id,
                        struct wl_resource *top) {
  (void)manager;
  assert(top);
  struct wl_resource *r = wl_resource_create(client, &ext_image_capture_source_v1_interface, 1, id);
  wl_resource_set_implementation(r, &wire_source_impl, NULL, NULL);
}
static const struct ext_foreign_toplevel_image_capture_source_manager_v1_interface
    wire_sources_impl = {wire_source, wire_destroy};
static void wire_sources(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
  (void)data;
  struct wl_resource *r = wl_resource_create(
      client, &ext_foreign_toplevel_image_capture_source_manager_v1_interface, version, id);
  wl_resource_set_implementation(r, &wire_sources_impl, NULL, NULL);
}
static void wire_attach(struct wl_client *client, struct wl_resource *frame,
                        struct wl_resource *buffer) {
  (void)client;
  (void)frame;
  wire_buffer = buffer;
}
static void wire_damage(struct wl_client *client, struct wl_resource *frame, int32_t x, int32_t y,
                        int32_t w, int32_t h) {
  (void)client;
  (void)frame;
  struct wl_shm_buffer *b = wl_shm_buffer_get(wire_buffer);
  assert(x == 0 && y == 0 && w == wl_shm_buffer_get_width(b) && h == wl_shm_buffer_get_height(b));
}
static void wire_capture(struct wl_client *client, struct wl_resource *frame) {
  (void)client;
  ++wire_captures;
  if (wire_captures > 3)
    return; // Static image leaves the next frame pending.
  if (wire_captures == 2) {
    char proceed;
    assert(read(wire_proceed, &proceed, 1) == 1 && proceed == '\n');
    ext_image_copy_capture_session_v1_send_buffer_size(wire_cursor_session, 1, 2);
    ext_image_copy_capture_session_v1_send_shm_format(wire_cursor_session, WL_SHM_FORMAT_ARGB8888);
    ext_image_copy_capture_session_v1_send_done(wire_cursor_session);
    ext_image_copy_capture_frame_v1_send_failed(
        frame, EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_BUFFER_CONSTRAINTS);
    return;
  }
  struct wl_shm_buffer *b = wl_shm_buffer_get(wire_buffer);
  assert(b && wl_shm_buffer_get_width(b) == (wire_captures == 1 ? 2 : 1) &&
         wl_shm_buffer_get_height(b) == (wire_captures == 1 ? 1 : 2));
  wl_shm_buffer_begin_access(b);
  uint32_t *pixels = wl_shm_buffer_get_data(b);
  pixels[0] = 0x80800000;
  pixels[1] = 0xff00ff00;
  wl_shm_buffer_end_access(b);
  ext_image_copy_capture_frame_v1_send_transform(frame, WL_OUTPUT_TRANSFORM_NORMAL);
  ext_image_copy_capture_frame_v1_send_damage(frame, 0, 0, wl_shm_buffer_get_width(b),
                                              wl_shm_buffer_get_height(b));
  ext_image_copy_capture_frame_v1_send_presentation_time(frame, 0, 1, 0);
  ext_image_copy_capture_frame_v1_send_ready(frame);
}
static const struct ext_image_copy_capture_frame_v1_interface wire_frame_impl = {
    wire_destroy, wire_attach, wire_damage, wire_capture};
static void wire_frame(struct wl_client *client, struct wl_resource *session, uint32_t id) {
  assert(wl_resource_get_user_data(session)); // Main-window capture must never be requested.
  struct wl_resource *r =
      wl_resource_create(client, &ext_image_copy_capture_frame_v1_interface, 1, id);
  wl_resource_set_implementation(r, &wire_frame_impl, NULL, NULL);
}
static const struct ext_image_copy_capture_session_v1_interface wire_session_impl = {wire_frame,
                                                                                     wire_destroy};
static void wire_constraints(struct wl_resource *r, uint32_t w, uint32_t h) {
  ext_image_copy_capture_session_v1_send_buffer_size(r, w, h);
  ext_image_copy_capture_session_v1_send_shm_format(r, WL_SHM_FORMAT_ARGB8888);
  ext_image_copy_capture_session_v1_send_done(r);
}
static void wire_main(struct wl_client *client, struct wl_resource *manager, uint32_t id,
                      struct wl_resource *source, uint32_t options) {
  (void)manager;
  assert(source && options == 0);
  struct wl_resource *r =
      wl_resource_create(client, &ext_image_copy_capture_session_v1_interface, 1, id);
  wl_resource_set_implementation(r, &wire_session_impl, NULL, NULL);
  wire_constraints(r, 16, 16);
}
static void wire_get_session(struct wl_client *client, struct wl_resource *cursor, uint32_t id) {
  (void)cursor;
  struct wl_resource *r =
      wl_resource_create(client, &ext_image_copy_capture_session_v1_interface, 1, id);
  wl_resource_set_implementation(r, &wire_session_impl, (void *)1, NULL);
  wire_cursor_session = r;
  wire_constraints(r, 2, 1);
}
static const struct ext_image_copy_capture_cursor_session_v1_interface wire_cursor_impl = {
    wire_destroy, wire_get_session};
static void wire_cursor(struct wl_client *client, struct wl_resource *manager, uint32_t id,
                        struct wl_resource *source, struct wl_resource *pointer) {
  (void)manager;
  assert(source && pointer);
  struct wl_resource *r =
      wl_resource_create(client, &ext_image_copy_capture_cursor_session_v1_interface, 1, id);
  wl_resource_set_implementation(r, &wire_cursor_impl, NULL, NULL);
  ext_image_copy_capture_cursor_session_v1_send_enter(r);
  ext_image_copy_capture_cursor_session_v1_send_position(r, 5, 6);
  ext_image_copy_capture_cursor_session_v1_send_hotspot(r, 1, 0);
}
static const struct ext_image_copy_capture_manager_v1_interface wire_manager_impl = {
    wire_main, wire_cursor, wire_destroy};
static void wire_manager(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
  (void)data;
  struct wl_resource *r =
      wl_resource_create(client, &ext_image_copy_capture_manager_v1_interface, version, id);
  wl_resource_set_implementation(r, &wire_manager_impl, NULL, NULL);
}
int main(void) {
  alarm(5);
  int sockets[2];
  int proceed[2];
  assert(pipe(proceed) == 0);
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
  pid_t parent = getpid();
  pid_t child = fork();
  assert(child >= 0);
  if (!child) {
    assert(prctl(PR_SET_PDEATHSIG, SIGTERM) == 0 && getppid() == parent);
    close(sockets[0]);
    close(proceed[1]);
    wire_proceed = proceed[0];
    struct wl_display *display = wl_display_create();
    assert(display && wl_display_init_shm(display) == 0);
    assert(wl_global_create(display, &wl_seat_interface, 1, NULL, wire_seat));
    assert(wl_global_create(display, &ext_foreign_toplevel_list_v1_interface, 1, NULL, wire_list));
    assert(wl_global_create(display,
                            &ext_foreign_toplevel_image_capture_source_manager_v1_interface, 1,
                            NULL, wire_sources));
    assert(wl_global_create(display, &ext_image_copy_capture_manager_v1_interface, 1, NULL,
                            wire_manager));
    assert(wl_client_create(display, sockets[1]));
    wl_display_run(display);
    _exit(0);
  }
  close(sockets[1]);
  close(proceed[0]);
  char fd[32];
  snprintf(fd, sizeof(fd), "%d", sockets[0]);
  setenv("WAYLAND_SOCKET", fd, 1);
  struct dpe_wl_cursor *cursor = dpe_wl_cursor_create("synthetic-game");
  assert(cursor);
  const struct spa_meta_cursor *m = NULL;
  for (unsigned n = 0; n < 500 && !m; ++n) {
    m = dpe_wl_cursor_metadata(cursor, 16, 16);
    const struct timespec pause = {0, 2000000};
    nanosleep(&pause, NULL);
  }
  assert(m && m->position.x == 5 && m->position.y == 6 && m->hotspot.x == 1);
  const struct spa_meta_bitmap *b = SPA_PTROFF(m, m->bitmap_offset, struct spa_meta_bitmap);
  const uint32_t *pixels = SPA_PTROFF(b, b->offset, const uint32_t);
  assert(b->size.width == 2 && pixels[0] == 0x80800000 && pixels[1] == 0xff00ff00);
  assert(write(proceed[1], "\n", 1) == 1);
  close(proceed[1]);
  for (unsigned n = 0; n < 500; ++n) {
    m = dpe_wl_cursor_metadata(cursor, 16, 16);
    if (m) {
      b = SPA_PTROFF(m, m->bitmap_offset, const struct spa_meta_bitmap);
      if (b->size.width == 1 && b->size.height == 2)
        break;
    }
    const struct timespec pause = {0, 2000000};
    nanosleep(&pause, NULL);
  }
  m = dpe_wl_cursor_metadata(cursor, 16, 16);
  assert(m);
  b = SPA_PTROFF(m, m->bitmap_offset, struct spa_meta_bitmap);
  assert(b->size.width == 1 && b->size.height == 2);
  dpe_wl_cursor_destroy(cursor); // Also tears down an outstanding cursor frame.
  assert(!dpe_wl_cursor_metadata(cursor, 16, 16));
  dpe_wl_cursor_destroy(cursor);
  kill(child, SIGTERM);
  int status;
  assert(waitpid(child, &status, 0) == child);
  return 0;
}
