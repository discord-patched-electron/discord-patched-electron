// SPDX-License-Identifier: MIT
#ifndef DPE_VKCAPTURE_CURSOR_H
#define DPE_VKCAPTURE_CURSOR_H
#include <spa/buffer/meta.h>
#include <spa/param/video/format-utils.h>
#include <stdint.h>
struct dpe_x11_cursor;
struct dpe_x11_cursor *dpe_x11_cursor_create(void);
// Optional X11 connection, shape/geometry events and bounded pointer polling.
// Metadata is valid on this thread until its next call or destroy. NULL hides it.
const void *dpe_x11_cursor_metadata(struct dpe_x11_cursor *, uint32_t window, uint32_t width,
                                    uint32_t height);
void dpe_x11_cursor_stats(struct dpe_x11_cursor *, uint64_t values[4]);
void dpe_x11_cursor_destroy(struct dpe_x11_cursor *);
#endif
