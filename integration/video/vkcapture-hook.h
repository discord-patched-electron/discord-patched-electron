// SPDX-License-Identifier: MIT
#ifndef DPE_VKCAPTURE_HOOK_H
#define DPE_VKCAPTURE_HOOK_H
#include "vkcapture-wire.h"
bool dpe_hook_enabled(void);
// The selected consumer requests an explicitly described direct-import texture.
bool dpe_hook_direct(void);
bool dpe_hook_enter(void);
void dpe_hook_leave(void);
void dpe_hook_init(void);
void dpe_hook_identity(const uint8_t uuid[16]);
void dpe_hook_update(void);
bool dpe_hook_starting(void);
bool dpe_hook_stopping(void);
bool dpe_hook_active(void);
void dpe_hook_stop(void);
// Report one failed initial direct allocation and wait for a new control request.
// The graphics owner must first clean any partially allocated resources.
void dpe_hook_unavailable(void);
void dpe_hook_texture(int width, int height, int format, int strides[4], int offsets[4],
                      uint64_t modifier, bool flip, uint32_t color, int count, int fds[4],
                      uint32_t window);
bool dpe_hook_due(void);
// Drop an admitted frame before GPU submission; retain the capture deadline.
void dpe_hook_cancel(void);
// Called only after a zero-timeout graphics fence reports completion.
void dpe_hook_complete(bool success);
// Takes ownership of an exported sync_file; the consumer waits before sampling.
void dpe_hook_complete_fenced(int fd);
bool dpe_hook_device(const uint8_t uuid[16]);
#endif
