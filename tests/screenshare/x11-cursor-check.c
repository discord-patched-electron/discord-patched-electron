// Owned synthetic Xvfb window/cursor only; no desktop or camera pixels are read.
#define _POSIX_C_SOURCE 200809L
#include "../../integration/video/vkcapture-cursor.h"
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <xcb/xcb.h>
static void tick(void) {
  struct timespec time = {.tv_nsec = 4000000};
  nanosleep(&time, NULL);
}
static void sync_server(xcb_connection_t *connection) {
  xcb_get_input_focus_reply_t *reply =
      xcb_get_input_focus_reply(connection, xcb_get_input_focus(connection), NULL);
  assert(reply);
  free(reply);
}
static xcb_cursor_t cursor(xcb_connection_t *connection, xcb_screen_t *screen, bool blue) {
  xcb_pixmap_t pixmap = xcb_generate_id(connection);
  xcb_create_pixmap(connection, 1, pixmap, screen->root, 2, 2);
  xcb_gcontext_t gc = xcb_generate_id(connection);
  uint32_t foreground = 1;
  xcb_create_gc(connection, gc, pixmap, XCB_GC_FOREGROUND, &foreground);
  xcb_rectangle_t rectangle = {0, 0, 2, 2};
  xcb_poly_fill_rectangle(connection, pixmap, gc, 1, &rectangle);
  xcb_cursor_t result = xcb_generate_id(connection);
  xcb_create_cursor(connection, result, pixmap, pixmap, blue ? 0 : 65535, 0, blue ? 65535 : 0, 0, 0,
                    0, 1, 1);
  xcb_free_gc(connection, gc);
  xcb_free_pixmap(connection, pixmap);
  return result;
}
static const struct spa_meta_cursor *ready(struct dpe_x11_cursor *capture, uint32_t window,
                                           uint32_t width) {
  for (unsigned i = 0; i < 250; i++) {
    const struct spa_meta_cursor *meta = dpe_x11_cursor_metadata(capture, window, width, 16);
    if (meta && meta->id)
      return meta;
    tick();
  }
  return NULL;
}
int main(void) {
  alarm(8);
  int index = 0;
  xcb_connection_t *connection = xcb_connect(NULL, &index);
  assert(connection && !xcb_connection_has_error(connection));
  xcb_screen_iterator_t screens = xcb_setup_roots_iterator(xcb_get_setup(connection));
  while (index-- > 0)
    xcb_screen_next(&screens);
  assert(screens.rem > 0);
  xcb_screen_t *screen = screens.data;
  xcb_window_t window = xcb_generate_id(connection);
  xcb_create_window(connection, XCB_COPY_FROM_PARENT, window, screen->root, 100, 100, 16, 16, 0,
                    XCB_WINDOW_CLASS_INPUT_OUTPUT, screen->root_visual, 0, NULL);
  xcb_cursor_t first = cursor(connection, screen, false), second = cursor(connection, screen, true);
  uint32_t shape = first;
  xcb_change_window_attributes(connection, window, XCB_CW_CURSOR, &shape);
  xcb_map_window(connection, window);
  xcb_warp_pointer(connection, XCB_NONE, window, 0, 0, 0, 0, 5, 6);
  sync_server(connection);
  struct dpe_x11_cursor *capture = dpe_x11_cursor_create();
  assert(capture);
  const struct spa_meta_cursor *meta = ready(capture, window, 16);
  assert(meta);
  assert(meta->position.x == 5 && meta->position.y == 6 && meta->hotspot.x == 1 &&
         meta->hotspot.y == 1);
  const struct spa_meta_bitmap *bitmap =
      SPA_PTROFF(meta, meta->bitmap_offset, const struct spa_meta_bitmap);
  assert(bitmap->size.width == 2 && bitmap->size.height == 2 && bitmap->stride == 8 &&
         bitmap->format == SPA_VIDEO_FORMAT_BGRA);
  const uint32_t *pixels = SPA_PTROFF(bitmap, bitmap->offset, const uint32_t);
  assert(pixels[0] == UINT32_C(0xffff0000));
  uint32_t serial = meta->id;
  uint64_t before[4], after[4];
  dpe_x11_cursor_stats(capture, before);
  for (unsigned i = 0; i < 120; i++) {
    assert(dpe_x11_cursor_metadata(capture, window, 16, 16));
    tick();
  }
  dpe_x11_cursor_stats(capture, after);
  assert(after[1] == before[1] && after[2] == before[2]);
  assert(after[0] > before[0] && after[0] - before[0] <= 45);
  printf("Stable cursor: %llu pointer requests, 0 geometry requests, 0 image requests across 120 "
         "updates\n",
         (unsigned long long)(after[0] - before[0]));
  shape = second;
  xcb_change_window_attributes(connection, window, XCB_CW_CURSOR, &shape);
  sync_server(connection);
  bool changed = false;
  for (unsigned i = 0; i < 250; i++) {
    meta = dpe_x11_cursor_metadata(capture, window, 16, 16);
    if (meta && meta->id != serial) {
      changed = true;
      break;
    }
    tick();
  }
  assert(changed);
  bitmap = SPA_PTROFF(meta, meta->bitmap_offset, const struct spa_meta_bitmap);
  pixels = SPA_PTROFF(bitmap, bitmap->offset, const uint32_t);
  assert(pixels[0] == UINT32_C(0xff0000ff));
  dpe_x11_cursor_stats(capture, after);
  assert(after[2] > before[2] && after[2] <= before[2] + 2);
  uint32_t dimensions[2] = {32, 16};
  xcb_configure_window(connection, window, XCB_CONFIG_WINDOW_WIDTH | XCB_CONFIG_WINDOW_HEIGHT,
                       dimensions);
  sync_server(connection);
  assert(ready(capture, window, 32));
  assert(!dpe_x11_cursor_metadata(capture, window, 16, 16));
  dpe_x11_cursor_stats(capture, after);
  assert(after[1] == before[1]);
  xcb_unmap_window(connection, window);
  sync_server(connection);
  tick();
  assert(!dpe_x11_cursor_metadata(capture, window, 32, 16));
  xcb_map_window(connection, window);
  sync_server(connection);
  assert(ready(capture, window, 32));
  xcb_destroy_window(connection, window);
  sync_server(connection);
  tick();
  assert(!dpe_x11_cursor_metadata(capture, window, 32, 16));
  dpe_x11_cursor_destroy(capture);
  dpe_x11_cursor_destroy(capture);
  assert(!dpe_x11_cursor_metadata(capture, window, 32, 16));
  xcb_free_cursor(connection, first);
  xcb_free_cursor(connection, second);
  xcb_disconnect(connection);
  return 0;
}
