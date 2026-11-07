// SPDX-License-Identifier: MIT
#ifndef DPE_VKCAPTURE_WAYLAND_CURSOR_H
#define DPE_VKCAPTURE_WAYLAND_CURSOR_H
#include <stdint.h>
struct dpe_wl_cursor;
// Exact ext-foreign-toplevel identifier; never match titles or pick an output.
struct dpe_wl_cursor *dpe_wl_cursor_create(const char *identifier);
// Returns bounded BGRA spa_meta_cursor storage, or NULL when hidden/unsupported.
// Pumps its dedicated Wayland socket without waiting. All calls and use of the
// returned storage occur on the creating thread, before its next call/destroy.
const void *dpe_wl_cursor_metadata(struct dpe_wl_cursor *, uint32_t width, uint32_t height);
void dpe_wl_cursor_destroy(struct dpe_wl_cursor *);
#endif
