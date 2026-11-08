// SPDX-License-Identifier: MIT
#ifndef DPE_VKCAPTURE_GL_SYNC_H
#define DPE_VKCAPTURE_GL_SYNC_H
#include <stdbool.h>
#include <stdint.h>
#include <vulkan/vulkan.h>

struct dpe_gl_sync_egl {
  void *display;
  void *(*create)(void *, unsigned, const int *);
  unsigned (*destroy)(void *, void *);
  int (*dup_fd)(void *, void *);
  unsigned (*get)(void *, void *, int, int *);
  void (*flush)(void);
};

bool dpe_gl_sync_requested(void);
// Validate the exact allocation's simultaneous GL opaque-FD and DMA-BUF exports.
bool dpe_gl_image_exportable(PFN_vkGetPhysicalDeviceImageFormatProperties2KHR, VkPhysicalDevice,
                             const VkImageCreateInfo *, uint64_t modifier);
// Probe without creating a device. Returns a graphics queue family, or -1.
int dpe_gl_sync_probe(void *(*gl_proc)(const char *), void *(*current_context)(void),
                      PFN_vkGetInstanceProcAddr, VkInstance, VkPhysicalDevice, bool native_fence);
bool dpe_gl_sync_create(PFN_vkGetDeviceProcAddr, VkDevice, unsigned family,
                        const struct dpe_gl_sync_egl *);
// 1: acquire queued; 0: previous GPU work still pending; -1: fatal error.
int dpe_gl_sync_begin(unsigned texture);
// Takes no fd ownership. Returns an owned sync_file, -1 for already completed,
// or -2 on failure. Only call after a successful begin and full texture overwrite.
int dpe_gl_sync_end(unsigned texture);
bool dpe_gl_sync_active(void);
bool dpe_gl_sync_uses_egl(void);
void dpe_gl_sync_collect(void);
void dpe_gl_sync_retire(void);
// Call after successful EGL/GLX context destruction; pointer reuse is not identity.
void dpe_gl_sync_context_destroyed(void *context);
#endif
