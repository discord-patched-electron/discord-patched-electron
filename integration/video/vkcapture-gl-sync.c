// SPDX-License-Identifier: MIT
// GLX/EGL completion through exported Vulkan semaphores. No pixel copies.
// Full overwrite permits a GL_NONE/UNDEFINED acquire after the consumer's real
// GPU release; the native reader's final image layout need not be guessed.
#define _GNU_SOURCE
#include "vkcapture-gl-sync.h"
#include <GL/glcorearb.h>
#include <GL/glext.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_RETIRED 4
static struct {
  void *(*context)(void);
  PFNGLGETERRORPROC error;
  PFNGLGENSEMAPHORESEXTPROC gen;
  PFNGLDELETESEMAPHORESEXTPROC destroy;
  PFNGLIMPORTSEMAPHOREFDEXTPROC import;
  PFNGLWAITSEMAPHOREEXTPROC wait;
  PFNGLSIGNALSEMAPHOREEXTPROC signal;
} gl;

struct bridge {
  void *context;
  void *(*current_context)(void);
  PFNGLDELETESEMAPHORESEXTPROC delete_gl;
  VkDevice device;
  VkQueue queue;
  VkSemaphore acquire, complete, output;
  VkFence before, after;
  GLuint gl_acquire, gl_complete;
  bool before_busy, after_busy, begun, primed, untracked_gl;
  struct dpe_gl_sync_egl egl;
  void *egl_fence;
  struct bridge *next;
  PFN_vkCreateSemaphore create_semaphore;
  PFN_vkDestroySemaphore destroy_semaphore;
  PFN_vkGetSemaphoreFdKHR export_semaphore;
  PFN_vkCreateFence create_fence;
  PFN_vkDestroyFence destroy_fence;
  PFN_vkGetFenceStatus fence_status;
  PFN_vkResetFences reset_fences;
  PFN_vkQueueSubmit submit;
};
static struct bridge *current, *retired;
static unsigned retired_count;

bool dpe_gl_sync_requested(void) {
  const char *value = getenv("DPE_GL_SEMAPHORE_SYNC");
  return !value || strcmp(value, "0") != 0;
}
bool dpe_gl_image_exportable(PFN_vkGetPhysicalDeviceImageFormatProperties2KHR query,
                             VkPhysicalDevice physical, const VkImageCreateInfo *image,
                             uint64_t modifier) {
  if (!query || !image ||
      (image->tiling != VK_IMAGE_TILING_LINEAR &&
       image->tiling != VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT))
    return false;
  const VkExternalMemoryHandleTypeFlagBits handles[] = {
      VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
  const VkExternalMemoryHandleTypeFlags both = handles[0] | handles[1];
  for (unsigned i = 0; i < 2; ++i) {
    VkPhysicalDeviceImageDrmFormatModifierInfoEXT drm = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT,
        .drmFormatModifier = modifier,
        .sharingMode = image->sharingMode,
        .queueFamilyIndexCount = image->queueFamilyIndexCount,
        .pQueueFamilyIndices = image->pQueueFamilyIndices};
    VkPhysicalDeviceExternalImageFormatInfo external = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
        .pNext = image->tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT ? &drm : NULL,
        .handleType = handles[i]};
    VkPhysicalDeviceImageFormatInfo2 info = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
        .pNext = &external,
        .format = image->format,
        .type = image->imageType,
        .tiling = image->tiling,
        .usage = image->usage,
        .flags = image->flags};
    VkExternalImageFormatProperties export = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES};
    VkImageFormatProperties2 properties = {.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
                                           .pNext = &export};
    if (query(physical, &info, &properties) != VK_SUCCESS)
      return false;
    const VkExternalMemoryProperties *memory = &export.externalMemoryProperties;
    const VkImageFormatProperties *limits = &properties.imageFormatProperties;
    if (!(memory->externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT) ||
        (memory->compatibleHandleTypes & both) != both ||
        limits->maxExtent.width < image->extent.width ||
        limits->maxExtent.height < image->extent.height ||
        limits->maxExtent.depth < image->extent.depth || limits->maxMipLevels < image->mipLevels ||
        limits->maxArrayLayers < image->arrayLayers || !(limits->sampleCounts & image->samples))
      return false;
    // DEDICATED_ONLY is valid: this allocator always binds a dedicated allocation.
  }
  return true;
}
static bool extension(PFNGLGETINTEGERVPROC integer, PFNGLGETSTRINGIPROC string,
                      const char *wanted) {
  GLint count = 0;
  integer(GL_NUM_EXTENSIONS, &count);
  if (count < 1 || count > 65536)
    return false;
  for (GLint i = 0; i < count; ++i) {
    const char *name = (const char *)string(GL_EXTENSIONS, (GLuint)i);
    if (name && !strcmp(name, wanted))
      return true;
  }
  return false;
}

int dpe_gl_sync_probe(void *(*get_gl)(const char *), void *(*current_context)(void),
                      PFN_vkGetInstanceProcAddr get_vk, VkInstance instance,
                      VkPhysicalDevice physical, bool native_fence) {
  PFNGLGETINTEGERVPROC integer = (void *)get_gl("glGetIntegerv");
  PFNGLGETSTRINGIPROC string = (void *)get_gl("glGetStringi");
  PFNGLGETUNSIGNEDBYTEVEXTPROC uuid = (void *)get_gl("glGetUnsignedBytevEXT");
  PFNGLGETUNSIGNEDBYTEI_VEXTPROC device_uuid = (void *)get_gl("glGetUnsignedBytei_vEXT");
  PFN_vkGetPhysicalDeviceProperties2KHR properties =
      (void *)get_vk(instance, "vkGetPhysicalDeviceProperties2KHR");
  PFN_vkGetPhysicalDeviceExternalSemaphorePropertiesKHR semaphore_properties =
      (void *)get_vk(instance, "vkGetPhysicalDeviceExternalSemaphorePropertiesKHR");
  PFN_vkGetPhysicalDeviceQueueFamilyProperties queues =
      (void *)get_vk(instance, "vkGetPhysicalDeviceQueueFamilyProperties");
  if (!current_context || !current_context() || !integer || !string || !uuid || !device_uuid ||
      !properties || !semaphore_properties || !queues ||
      !extension(integer, string, "GL_EXT_semaphore") ||
      !extension(integer, string, "GL_EXT_semaphore_fd"))
    return -1;
  gl.context = current_context;
#define GL_PROC(field, name)                                                                       \
  gl.field = (void *)get_gl(name);                                                                 \
  if (!gl.field)                                                                                   \
  return -1
  GL_PROC(error, "glGetError");
  GL_PROC(gen, "glGenSemaphoresEXT");
  GL_PROC(destroy, "glDeleteSemaphoresEXT");
  GL_PROC(import, "glImportSemaphoreFdEXT");
  GL_PROC(wait, "glWaitSemaphoreEXT");
  GL_PROC(signal, "glSignalSemaphoreEXT");
#undef GL_PROC
  VkPhysicalDeviceIDProperties id = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES};
  VkPhysicalDeviceProperties2 props = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
                                       .pNext = &id};
  properties(physical, &props);
  uint8_t driver[VK_UUID_SIZE], device[VK_UUID_SIZE];
  uuid(GL_DRIVER_UUID_EXT, driver);
  device_uuid(GL_DEVICE_UUID_EXT, 0, device);
  if (gl.error() != GL_NO_ERROR || memcmp(driver, id.driverUUID, VK_UUID_SIZE) ||
      memcmp(device, id.deviceUUID, VK_UUID_SIZE))
    return -1;
  const VkExternalSemaphoreHandleTypeFlagBits types[] = {
      VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT,
      VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT};
  for (unsigned i = 0; i < (native_fence ? 1u : 2u); ++i) {
    VkPhysicalDeviceExternalSemaphoreInfo info = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_SEMAPHORE_INFO, .handleType = types[i]};
    VkExternalSemaphoreProperties support = {.sType =
                                                 VK_STRUCTURE_TYPE_EXTERNAL_SEMAPHORE_PROPERTIES};
    semaphore_properties(physical, &info, &support);
    if (!(support.externalSemaphoreFeatures & VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT))
      return -1;
  }
  uint32_t count = 0;
  queues(physical, &count, NULL);
  if (!count || count > 64)
    return -1;
  VkQueueFamilyProperties families[64];
  queues(physical, &count, families);
  for (uint32_t i = 0; i < count; ++i)
    if (families[i].queueCount && (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT))
      return (int)i;
  return -1;
}

static int finished(struct bridge *b) {
  if (b->egl.display) {
    if (b->untracked_gl && b->context)
      return 0; // A failed fence creation cannot prove pending GL work completed.
    if (!b->egl_fence)
      return 1;
    int status = 0;
    if (!b->egl.get(b->egl.display, b->egl_fence, 0x30f1 /* EGL_SYNC_STATUS_KHR */, &status))
      return -1;
    return status == 0x30f2 /* EGL_SIGNALED_KHR */;
  }
  VkResult before = b->before_busy ? b->fence_status(b->device, b->before) : VK_SUCCESS;
  VkResult after = b->after_busy ? b->fence_status(b->device, b->after) : VK_SUCCESS;
  if ((before != VK_SUCCESS && before != VK_NOT_READY) ||
      (after != VK_SUCCESS && after != VK_NOT_READY))
    return -1;
  if (before == VK_SUCCESS)
    b->before_busy = false;
  if (after == VK_SUCCESS)
    b->after_busy = false;
  return before == VK_SUCCESS && after == VK_SUCCESS;
}
static void destroy(struct bridge *b) {
  if (b->egl_fence)
    b->egl.destroy(b->egl.display, b->egl_fence);
  if (b->acquire)
    b->destroy_semaphore(b->device, b->acquire, NULL);
  if (b->complete)
    b->destroy_semaphore(b->device, b->complete, NULL);
  if (b->output)
    b->destroy_semaphore(b->device, b->output, NULL);
  if (b->before)
    b->destroy_fence(b->device, b->before, NULL);
  if (b->after)
    b->destroy_fence(b->device, b->after, NULL);
  free(b);
}
void dpe_gl_sync_collect(void) {
  struct bridge **link = &retired;
  while (*link) {
    struct bridge *b = *link;
    // Device errors are not proof of completion. Retain a bounded allocation
    // rather than destroying handles that might still belong to a GPU queue.
    if (finished(b) == 1 && (!b->context || b->current_context() == b->context)) {
      *link = b->next;
      --retired_count;
      if (b->gl_acquire)
        b->delete_gl(1, &b->gl_acquire);
      if (b->gl_complete)
        b->delete_gl(1, &b->gl_complete);
      destroy(b);
    } else
      link = &b->next;
  }
}
void dpe_gl_sync_retire(void) {
  if (!current)
    return;
  struct bridge *b = current;
  current = NULL;
  // A different context can contain unrelated objects with identical GL names.
  // Keep a bounded retired entry until its GPU work and owning context permit
  // cleanup. Context destruction itself releases its GL object references.
  b->next = retired;
  retired = b;
  ++retired_count;
  dpe_gl_sync_collect();
}
void dpe_gl_sync_context_destroyed(void *context) {
  // Context teardown owns any remaining GL references. Never address those
  // names through a later context which happens to reuse the same pointer.
  for (struct bridge *b = retired; b; b = b->next) {
    if (b->context == context) {
      b->context = NULL;
      b->gl_acquire = b->gl_complete = 0;
    }
  }
  dpe_gl_sync_collect();
}
static bool semaphore(struct bridge *b, VkSemaphore *handle,
                      VkExternalSemaphoreHandleTypeFlagBits type, GLuint *name) {
  VkExportSemaphoreCreateInfo export = {.sType = VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO,
                                        .handleTypes = type};
  VkSemaphoreCreateInfo info = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO, .pNext = &export};
  if (b->create_semaphore(b->device, &info, NULL, handle) != VK_SUCCESS)
    return false;
  if (!name)
    return true;
  VkSemaphoreGetFdInfoKHR fd_info = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR,
                                     .semaphore = *handle,
                                     .handleType = type};
  int fd = -1;
  if (b->export_semaphore(b->device, &fd_info, &fd) != VK_SUCCESS || fd < 0)
    return false;
  gl.gen(1, name);
  if (!*name || gl.error() != GL_NO_ERROR) {
    close(fd);
    return false;
  }
  gl.import(*name, GL_HANDLE_TYPE_OPAQUE_FD_EXT, fd);
  if (gl.error() != GL_NO_ERROR) {
    close(fd);
    return false;
  }
  return true; // Successful GL import owns fd.
}
bool dpe_gl_sync_create(PFN_vkGetDeviceProcAddr get, VkDevice device, unsigned family,
                        const struct dpe_gl_sync_egl *egl) {
  dpe_gl_sync_collect();
  if (current || retired_count >= MAX_RETIRED || !gl.context || !gl.context())
    return false;
  struct bridge *b = calloc(1, sizeof(*b));
  if (!b)
    return false;
  b->device = device;
  b->context = gl.context();
  b->current_context = gl.context;
  b->delete_gl = gl.destroy;
  if (egl) {
    if (!egl->display || !egl->create || !egl->destroy || !egl->dup_fd || !egl->get || !egl->flush)
      goto unavailable;
    b->egl = *egl;
  }
#define VK_PROC(field, name)                                                                       \
  b->field = (void *)get(device, name);                                                            \
  if (!b->field)                                                                                   \
  goto unavailable
  VK_PROC(create_semaphore, "vkCreateSemaphore");
  VK_PROC(destroy_semaphore, "vkDestroySemaphore");
  VK_PROC(export_semaphore, "vkGetSemaphoreFdKHR");
  VK_PROC(create_fence, "vkCreateFence");
  VK_PROC(destroy_fence, "vkDestroyFence");
  VK_PROC(fence_status, "vkGetFenceStatus");
  VK_PROC(reset_fences, "vkResetFences");
  VK_PROC(submit, "vkQueueSubmit");
#undef VK_PROC
  PFN_vkGetDeviceQueue queue = (void *)get(device, "vkGetDeviceQueue");
  if (!queue)
    goto unavailable;
  queue(device, family, 0, &b->queue);
  if (!b->queue)
    goto unavailable;
  current = b;
  if (!semaphore(b, &b->acquire, VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT, &b->gl_acquire))
    goto fail;
  if (egl)
    return true; // GL/EGL owns the whole frame cycle; no Vulkan queue submission.
  if (!semaphore(b, &b->complete, VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT,
                 &b->gl_complete) ||
      !semaphore(b, &b->output, VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT, NULL))
    goto fail;
  VkFenceCreateInfo fence = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  if (b->create_fence(device, &fence, NULL, &b->before) != VK_SUCCESS ||
      b->create_fence(device, &fence, NULL, &b->after) != VK_SUCCESS)
    goto fail;
  return true;
fail:
  dpe_gl_sync_retire();
  return false;
unavailable:
  free(b);
  return false;
}
bool dpe_gl_sync_active(void) { return current != NULL; }
bool dpe_gl_sync_uses_egl(void) { return current && current->egl.display; }
int dpe_gl_sync_begin(unsigned texture) {
  struct bridge *b = current;
  if (!b || b->begun)
    return -1;
  int ready = finished(b);
  if (ready != 1)
    return ready;
  if (b->egl_fence) {
    b->egl.destroy(b->egl.display, b->egl_fence);
    b->egl_fence = NULL;
  }
  if (!b->primed) {
    if (b->egl.display) {
      b->untracked_gl = true;
      gl.signal(b->gl_acquire, 0, NULL, 0, NULL, NULL);
      if (gl.error() != GL_NO_ERROR)
        return -1;
    } else {
      if (b->reset_fences(b->device, 1, &b->before) != VK_SUCCESS)
        return -1;
      VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
                             .signalSemaphoreCount = 1,
                             .pSignalSemaphores = &b->acquire};
      if (b->submit(b->queue, 1, &submit, b->before) != VK_SUCCESS)
        return -1;
      b->before_busy = true;
    }
    b->primed = true;
  }
  if (b->egl.display)
    b->untracked_gl = true;
  GLenum layout = GL_NONE; // Discard: the following full blit defines every pixel.
  gl.wait(b->gl_acquire, 0, NULL, 1, &texture, &layout);
  if (gl.error() != GL_NO_ERROR)
    return -1;
  b->begun = true;
  return 1;
}
int dpe_gl_sync_end(unsigned texture) {
  struct bridge *b = current;
  if (!b || !b->begun)
    return -2;
  GLenum layout = GL_LAYOUT_GENERAL_EXT;
  gl.signal(b->egl.display ? b->gl_acquire : b->gl_complete, 0, NULL, 1, &texture, &layout);
  if (gl.error() != GL_NO_ERROR)
    return -2;
  if (b->egl.display) {
    b->egl_fence = b->egl.create(b->egl.display, 0x3144 /* EGL_SYNC_NATIVE_FENCE_ANDROID */, NULL);
    if (!b->egl_fence)
      return -2;
    // The native fence is inserted after the explicit GENERAL release, so its
    // completion includes that visibility transition, not just the blit's end.
    b->egl.flush();
    b->untracked_gl = false;
    b->begun = false;
    int fd = b->egl.dup_fd(b->egl.display, b->egl_fence);
    return fd < 0 ? -2 : fd;
  }
  if (b->reset_fences(b->device, 1, &b->after) != VK_SUCCESS)
    return -2;
  VkPipelineStageFlags stage = VK_PIPELINE_STAGE_ALL_COMMANDS_BIT;
  VkSemaphore signals[] = {b->output, b->acquire};
  VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
                         .waitSemaphoreCount = 1,
                         .pWaitSemaphores = &b->complete,
                         .pWaitDstStageMask = &stage,
                         .signalSemaphoreCount = 2,
                         .pSignalSemaphores = signals};
  if (b->submit(b->queue, 1, &submit, b->after) != VK_SUCCESS)
    return -2;
  b->after_busy = true;
  b->begun = false;
  VkSemaphoreGetFdInfoKHR info = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR,
                                  .semaphore = b->output,
                                  .handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT};
  int fd = -1;
  if (b->export_semaphore(b->device, &info, &fd) != VK_SUCCESS)
    return -2;
  return fd;
}
