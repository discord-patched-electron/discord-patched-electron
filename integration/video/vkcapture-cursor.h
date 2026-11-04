// SPDX-License-Identifier: MIT
// Bounded asynchronous X11 cursor metadata. No game pixels leave the GPU.
#ifndef DPE_VKCAPTURE_CURSOR_H
#define DPE_VKCAPTURE_CURSOR_H
#include <dlfcn.h>
#include <spa/buffer/meta.h>
#include <spa/param/video/format-utils.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <xcb/xcb.h>
#include <xcb/xcbext.h>
#include <xcb/xfixes.h>
#define DPE_CURSOR_EDGE 256
#define DPE_CURSOR_META_SIZE                                                                       \
  (sizeof(struct spa_meta_cursor) + sizeof(struct spa_meta_bitmap) +                               \
   DPE_CURSOR_EDGE * DPE_CURSOR_EDGE * 4)
struct dpe_game_cursor {
  void *xcb, *fixes;
  xcb_connection_t *connection;
  uint32_t window;
  unsigned pointer_cookie, image_cookie, geometry_cookie;
  void *pointer, *image, *geometry;
  uint64_t requested;
  uint8_t metadata[DPE_CURSOR_META_SIZE];
  bool initialized;
  xcb_connection_t *(*connect)(const char *, int *);
  void (*disconnect)(xcb_connection_t *);
  int (*error)(xcb_connection_t *);
  int (*flush)(xcb_connection_t *);
  int (*poll_reply)(xcb_connection_t *, unsigned, void **, xcb_generic_error_t **);
  void (*discard)(xcb_connection_t *, unsigned);
  xcb_query_pointer_cookie_t (*query_pointer)(xcb_connection_t *, xcb_window_t);
  xcb_get_geometry_cookie_t (*get_geometry)(xcb_connection_t *, xcb_drawable_t);
  xcb_xfixes_get_cursor_image_cookie_t (*get_image)(xcb_connection_t *);
  xcb_xfixes_query_version_cookie_t (*version)(xcb_connection_t *, uint32_t, uint32_t);
};
static uint64_t dpe_cursor_now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static void dpe_cursor_pending_clear(struct dpe_game_cursor *c) {
  unsigned cookies[] = {c->pointer_cookie, c->image_cookie, c->geometry_cookie};
  for (unsigned i = 0; i < 3; i++)
    if (cookies[i] && c->connection)
      c->discard(c->connection, cookies[i]);
  free(c->pointer);
  free(c->image);
  free(c->geometry);
  c->pointer = c->image = c->geometry = NULL;
  c->pointer_cookie = c->image_cookie = c->geometry_cookie = 0;
}
static void dpe_cursor_destroy(struct dpe_game_cursor *c) {
  dpe_cursor_pending_clear(c);
  if (c->connection)
    c->disconnect(c->connection);
  if (c->fixes)
    dlclose(c->fixes);
  if (c->xcb)
    dlclose(c->xcb);
  memset(c, 0, sizeof(*c));
}
static bool dpe_cursor_init(struct dpe_game_cursor *c) {
  c->initialized = true;
  if (!getenv("DISPLAY"))
    return false;
  c->xcb = dlopen("libxcb.so.1", RTLD_NOW | RTLD_LOCAL);
  c->fixes = dlopen("libxcb-xfixes.so.0", RTLD_NOW | RTLD_LOCAL);
  if (!c->xcb || !c->fixes)
    return false;
#define LOAD(field, library, symbol)                                                               \
  do {                                                                                             \
    *(void **)(&c->field) = dlsym(c->library, symbol);                                             \
    if (!c->field)                                                                                 \
      return false;                                                                                \
  } while (0)
  LOAD(connect, xcb, "xcb_connect");
  LOAD(disconnect, xcb, "xcb_disconnect");
  LOAD(error, xcb, "xcb_connection_has_error");
  LOAD(flush, xcb, "xcb_flush");
  LOAD(poll_reply, xcb, "xcb_poll_for_reply");
  LOAD(discard, xcb, "xcb_discard_reply");
  LOAD(query_pointer, xcb, "xcb_query_pointer");
  LOAD(get_geometry, xcb, "xcb_get_geometry");
  LOAD(get_image, fixes, "xcb_xfixes_get_cursor_image");
  LOAD(version, fixes, "xcb_xfixes_query_version");
#undef LOAD
  c->connection = c->connect(NULL, NULL);
  if (!c->connection || c->error(c->connection))
    return false;
  c->discard(c->connection, c->version(c->connection, 4, 0).sequence);
  c->flush(c->connection);
  return true;
}
// Converts only server-validated bounded cursor data, independently testable.
static bool dpe_cursor_encode(void *output, const xcb_query_pointer_reply_t *p,
                              const xcb_get_geometry_reply_t *g,
                              const xcb_xfixes_get_cursor_image_reply_t *i, uint32_t width,
                              uint32_t height) {
  if (!p || !g || !i || !p->same_screen || !g->width || !g->height || !i->width || !i->height ||
      i->width > DPE_CURSOR_EDGE || i->height > DPE_CURSOR_EDGE ||
      i->length < (uint32_t)i->width * i->height || p->win_x < 0 || p->win_y < 0 ||
      p->win_x >= g->width || p->win_y >= g->height || width != g->width || height != g->height)
    return false;
  struct spa_meta_cursor *meta = output;
  *meta = (struct spa_meta_cursor){.id = i->cursor_serial ? i->cursor_serial : 1,
                                   .position = {p->win_x, p->win_y},
                                   .hotspot = {i->xhot, i->yhot},
                                   .bitmap_offset = sizeof(*meta)};
  struct spa_meta_bitmap *bitmap = SPA_PTROFF(meta, meta->bitmap_offset, struct spa_meta_bitmap);
  *bitmap = (struct spa_meta_bitmap){.format = SPA_VIDEO_FORMAT_BGRA,
                                     .size = {i->width, i->height},
                                     .stride = i->width * 4,
                                     .offset = sizeof(*bitmap)};
  memcpy(SPA_PTROFF(bitmap, bitmap->offset, void), i + 1, (size_t)i->width * i->height * 4);
  return true;
}
static void dpe_cursor_update(struct dpe_game_cursor *c, uint32_t window, uint32_t width,
                              uint32_t height) {
  if (!window || (!c->initialized && !dpe_cursor_init(c)) || !c->connection ||
      c->error(c->connection))
    return;
  uint64_t now = dpe_cursor_now();
  if (c->window != window || ((c->pointer_cookie || c->image_cookie || c->geometry_cookie) &&
                              now - c->requested > 100000000)) {
    dpe_cursor_pending_clear(c);
    memset(c->metadata, 0, sizeof(struct spa_meta_cursor));
    c->window = window;
  }
  unsigned *cookies[] = {&c->pointer_cookie, &c->image_cookie, &c->geometry_cookie};
  void **replies[] = {&c->pointer, &c->image, &c->geometry};
  for (unsigned n = 0; n < 3; n++) {
    if (!*cookies[n])
      continue;
    xcb_generic_error_t *error = NULL;
    if (c->poll_reply(c->connection, *cookies[n], replies[n], &error)) {
      *cookies[n] = 0;
      if (error || !*replies[n]) {
        free(error);
        dpe_cursor_pending_clear(c);
        memset(c->metadata, 0, sizeof(struct spa_meta_cursor));
        break;
      }
    }
    free(error);
  }
  if (c->pointer && c->image && c->geometry) {
    memset(c->metadata, 0, sizeof(struct spa_meta_cursor));
    dpe_cursor_encode(c->metadata, c->pointer, c->geometry, c->image, width, height);
    dpe_cursor_pending_clear(c);
  }
  if (!c->pointer_cookie && !c->image_cookie && !c->geometry_cookie &&
      now - c->requested >= 16000000) {
    c->requested = now;
    c->pointer_cookie = c->query_pointer(c->connection, window).sequence;
    c->geometry_cookie = c->get_geometry(c->connection, window).sequence;
    c->image_cookie = c->get_image(c->connection).sequence;
    c->flush(c->connection);
  }
}
#endif
