// SPDX-License-Identifier: MIT
// Driver-independent semaphore ownership/order regression; never opens a GPU.
#include "../../integration/video/vkcapture-gl-sync.c"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>

#define HANDLE(type, n) ((type)(uintptr_t)(n))
static bool missing_extension, mismatch_uuid, no_export, fail_import;
static bool no_sync_export, fail_egl_create;
static bool fail_submit, fail_export, completed = true, device_lost;
static unsigned sem_count, fence_count, gl_count, destroyed, submissions;
static unsigned import_count, order_count;
static char order[128];
static int last_fd = -1;
static GLenum gl_error;
static unsigned image_queries, image_failure;
static VkResult fake_image_properties(VkPhysicalDevice physical,
                                      const VkPhysicalDeviceImageFormatInfo2 *info,
                                      VkImageFormatProperties2 *properties) {
  assert(physical && info->format == VK_FORMAT_B8G8R8A8_UNORM);
  assert(info->type == VK_IMAGE_TYPE_2D && info->tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT);
  assert(info->usage == VK_IMAGE_USAGE_TRANSFER_DST_BIT && !info->flags);
  const VkPhysicalDeviceExternalImageFormatInfo *external = info->pNext;
  const VkPhysicalDeviceImageDrmFormatModifierInfoEXT *drm = external->pNext;
  assert(drm && !drm->drmFormatModifier && drm->sharingMode == VK_SHARING_MODE_EXCLUSIVE);
  assert(external->handleType == VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT ||
         external->handleType == VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT);
  ++image_queries;
  if (image_failure == 1 && external->handleType == VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT)
    return VK_ERROR_FORMAT_NOT_SUPPORTED;
  VkExternalImageFormatProperties *export = properties->pNext;
  export->externalMemoryProperties.externalMemoryFeatures =
      VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT |
      (image_failure == 2 ? 0 : VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT);
  export->externalMemoryProperties.compatibleHandleTypes =
      VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT |
      (image_failure == 3 ? 0 : VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT);
  properties->imageFormatProperties = (VkImageFormatProperties){
      .maxExtent = {image_failure == 4 ? 1024 : 4096, 4096, 1},
      .maxMipLevels = 1,
      .maxArrayLayers = 1,
      .sampleCounts = image_failure == 5 ? VK_SAMPLE_COUNT_2_BIT : VK_SAMPLE_COUNT_1_BIT};
  return VK_SUCCESS;
}
static void *context = (void *)1;
static unsigned gl_deleted;
static void *fake_context(void) { return context; }

static void record(char c) {
  assert(order_count + 1 < sizeof(order));
  order[order_count++] = c;
  order[order_count] = 0;
}
static GLenum fake_error(void) {
  GLenum error = gl_error;
  gl_error = GL_NO_ERROR;
  return error;
}
static void fake_integer(GLenum name, GLint *value) {
  assert(name == GL_NUM_EXTENSIONS);
  *value = missing_extension ? 1 : 2;
}
static const GLubyte *fake_string(GLenum name, GLuint index) {
  assert(name == GL_EXTENSIONS);
  return (const GLubyte *)(index ? "GL_EXT_semaphore_fd" : "GL_EXT_semaphore");
}
static void fake_uuid(GLenum name, GLubyte *uuid) {
  assert(name == GL_DRIVER_UUID_EXT);
  memset(uuid, mismatch_uuid ? 3 : 1, VK_UUID_SIZE);
}
static void fake_device_uuid(GLenum name, GLuint index, GLubyte *uuid) {
  assert(name == GL_DEVICE_UUID_EXT && index == 0);
  memset(uuid, 2, VK_UUID_SIZE);
}
static void fake_gen(GLsizei count, GLuint *names) {
  assert(count == 1);
  *names = ++gl_count;
}
static void fake_delete(GLsizei count, const GLuint *names) {
  assert(count == 1 && *names);
  ++gl_deleted;
}
static void fake_import(GLuint name, GLenum type, GLint fd) {
  assert(name && type == GL_HANDLE_TYPE_OPAQUE_FD_EXT);
  assert(fd >= 0 && fcntl(fd, F_GETFD) >= 0);
  ++import_count;
  if (fail_import)
    gl_error = GL_INVALID_OPERATION;
  else
    assert(close(fd) == 0); // Successful GL import consumes the descriptor.
}
static void fake_wait(GLuint name, GLuint buffers, const GLuint *buffer_names, GLuint textures,
                      const GLuint *texture_names, const GLenum *layouts) {
  assert(name && !buffers && !buffer_names && textures == 1 && *texture_names == 99);
  assert(*layouts == GL_NONE); // Full overwrite must discard the old reader layout.
  record('W');
}
static void fake_signal(GLuint name, GLuint buffers, const GLuint *buffer_names, GLuint textures,
                        const GLuint *texture_names, const GLenum *layouts) {
  assert(name && !buffers && !buffer_names);
  if (!textures) {
    assert(!texture_names && !layouts);
    record('P');
    return;
  }
  assert(textures == 1 && *texture_names == 99);
  assert(*layouts == GL_LAYOUT_GENERAL_EXT);
  record('G');
}
static void *get_gl(const char *name) {
#define MAP(n, f)                                                                                  \
  if (!strcmp(name, n))                                                                            \
  return (void *)f
  MAP("glGetError", fake_error);
  MAP("glGetIntegerv", fake_integer);
  MAP("glGetStringi", fake_string);
  MAP("glGetUnsignedBytevEXT", fake_uuid);
  MAP("glGetUnsignedBytei_vEXT", fake_device_uuid);
  MAP("glGenSemaphoresEXT", fake_gen);
  MAP("glDeleteSemaphoresEXT", fake_delete);
  MAP("glImportSemaphoreFdEXT", fake_import);
  MAP("glWaitSemaphoreEXT", fake_wait);
  MAP("glSignalSemaphoreEXT", fake_signal);
  return NULL;
}
static void fake_properties(VkPhysicalDevice physical, VkPhysicalDeviceProperties2 *props) {
  assert(physical);
  VkPhysicalDeviceIDProperties *id = props->pNext;
  memset(id->driverUUID, 1, VK_UUID_SIZE);
  memset(id->deviceUUID, 2, VK_UUID_SIZE);
}
static void fake_support(VkPhysicalDevice physical,
                         const VkPhysicalDeviceExternalSemaphoreInfo *info,
                         VkExternalSemaphoreProperties *props) {
  assert(physical);
  assert(info->handleType == VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT ||
         info->handleType == VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT);
  props->externalSemaphoreFeatures =
      no_export ||
              (no_sync_export && info->handleType == VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT)
          ? 0
          : VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT;
}
static void fake_queues(VkPhysicalDevice physical, uint32_t *count,
                        VkQueueFamilyProperties *props) {
  assert(physical);
  *count = 2;
  if (props) {
    memset(props, 0, sizeof(*props) * 2);
    props[0].queueCount = 1;
    props[0].queueFlags = VK_QUEUE_TRANSFER_BIT;
    props[1].queueCount = 1;
    props[1].queueFlags = VK_QUEUE_GRAPHICS_BIT;
  }
}
static PFN_vkVoidFunction get_instance(VkInstance instance, const char *name) {
  assert(instance);
  MAP("vkGetPhysicalDeviceProperties2KHR", fake_properties);
  MAP("vkGetPhysicalDeviceExternalSemaphorePropertiesKHR", fake_support);
  MAP("vkGetPhysicalDeviceQueueFamilyProperties", fake_queues);
  return NULL;
}
static VkResult fake_create_sem(VkDevice device, const VkSemaphoreCreateInfo *info,
                                const VkAllocationCallbacks *allocator, VkSemaphore *out) {
  assert(device && !allocator);
  const VkExportSemaphoreCreateInfo *export = info->pNext;
  assert(export->handleTypes == VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT ||
         export->handleTypes == VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT);
  *out = HANDLE(VkSemaphore, ++sem_count);
  return VK_SUCCESS;
}
static void fake_destroy_sem(VkDevice device, VkSemaphore semaphore,
                             const VkAllocationCallbacks *allocator) {
  assert(device && semaphore && !allocator);
  ++destroyed;
}
static VkResult fake_export(VkDevice device, const VkSemaphoreGetFdInfoKHR *info, int *fd) {
  assert(device && info->semaphore);
  if (fail_export)
    return VK_ERROR_OUT_OF_HOST_MEMORY;
  if (info->handleType == VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT)
    record('E');
  *fd = last_fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
  assert(*fd >= 0);
  return VK_SUCCESS;
}
static VkResult fake_create_fence(VkDevice device, const VkFenceCreateInfo *info,
                                  const VkAllocationCallbacks *allocator, VkFence *out) {
  assert(device && !info->flags && !allocator);
  *out = HANDLE(VkFence, ++fence_count);
  return VK_SUCCESS;
}
static void fake_destroy_fence(VkDevice device, VkFence fence,
                               const VkAllocationCallbacks *allocator) {
  assert(device && fence && !allocator);
  ++destroyed;
}
static VkResult fake_status(VkDevice device, VkFence fence) {
  assert(device && fence);
  return device_lost ? VK_ERROR_DEVICE_LOST : completed ? VK_SUCCESS : VK_NOT_READY;
}
static VkResult fake_reset(VkDevice device, uint32_t count, const VkFence *fences) {
  assert(device && count == 1 && *fences);
  return VK_SUCCESS;
}
static VkResult fake_submit(VkQueue queue, uint32_t count, const VkSubmitInfo *info,
                            VkFence fence) {
  assert(queue && count == 1 && fence && !info->commandBufferCount);
  assert(*info->pSignalSemaphores);
  ++submissions;
  if (fail_submit)
    return VK_ERROR_OUT_OF_HOST_MEMORY;
  if (info->waitSemaphoreCount) {
    assert(info->waitSemaphoreCount == 1 && *info->pWaitSemaphores);
    assert(*info->pWaitDstStageMask == VK_PIPELINE_STAGE_ALL_COMMANDS_BIT);
    assert(info->signalSemaphoreCount == 2 && info->pSignalSemaphores[0] == current->output &&
           info->pSignalSemaphores[1] == current->acquire);
    record('S');
  } else {
    assert(info->signalSemaphoreCount == 1);
    record('A');
  }
  return VK_SUCCESS;
}
static void fake_queue(VkDevice device, uint32_t family, uint32_t index, VkQueue *queue) {
  assert(device && family == 1 && !index);
  *queue = HANDLE(VkQueue, 1);
}
static PFN_vkVoidFunction get_device(VkDevice device, const char *name) {
  assert(device);
  MAP("vkCreateSemaphore", fake_create_sem);
  MAP("vkDestroySemaphore", fake_destroy_sem);
  MAP("vkGetSemaphoreFdKHR", fake_export);
  MAP("vkCreateFence", fake_create_fence);
  MAP("vkDestroyFence", fake_destroy_fence);
  MAP("vkGetFenceStatus", fake_status);
  MAP("vkResetFences", fake_reset);
  MAP("vkQueueSubmit", fake_submit);
  MAP("vkGetDeviceQueue", fake_queue);
#undef MAP
  // In particular, no CPU/device/queue wait function is available to the hook.
  return NULL;
}
static int probe(void) {
  return dpe_gl_sync_probe(get_gl, fake_context, get_instance, HANDLE(VkInstance, 1),
                           HANDLE(VkPhysicalDevice, 1), false);
}
static bool create(void) { return dpe_gl_sync_create(get_device, HANDLE(VkDevice, 1), 1, NULL); }
static void *fake_egl_create(void *display, unsigned type, const int *attrs) {
  assert(display && type == 0x3144 && !attrs);
  record('C');
  return fail_egl_create ? NULL : (void *)3;
}
static unsigned fake_egl_destroy(void *display, void *sync) {
  assert(display && sync);
  return 1;
}
static int fake_egl_dup(void *display, void *sync) {
  assert(display && sync);
  record('E');
  return fail_export ? -1 : open("/dev/null", O_RDONLY | O_CLOEXEC);
}
static unsigned fake_egl_get(void *display, void *sync, int attr, int *value) {
  assert(display && sync && attr == 0x30f1);
  *value = completed ? 0x30f2 : 0x30f3;
  return !device_lost;
}
static void fake_flush(void) { record('F'); }
static bool create_egl(void) {
  const struct dpe_gl_sync_egl egl = {.display = (void *)1,
                                      .create = fake_egl_create,
                                      .destroy = fake_egl_destroy,
                                      .dup_fd = fake_egl_dup,
                                      .get = fake_egl_get,
                                      .flush = fake_flush};
  return dpe_gl_sync_create(get_device, HANDLE(VkDevice, 1), 1, &egl);
}
static void frame(void) {
  assert(dpe_gl_sync_begin(99) == 1);
  int fd = dpe_gl_sync_end(99);
  assert(fd >= 0 && fcntl(fd, F_GETFD) >= 0);
  assert(close(fd) == 0);
}
int main(void) {
  VkImageCreateInfo image = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                             .imageType = VK_IMAGE_TYPE_2D,
                             .format = VK_FORMAT_B8G8R8A8_UNORM,
                             .extent = {1920, 1080, 1},
                             .mipLevels = 1,
                             .arrayLayers = 1,
                             .samples = VK_SAMPLE_COUNT_1_BIT,
                             .tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
                             .usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT,
                             .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
  assert(dpe_gl_image_exportable(fake_image_properties, HANDLE(VkPhysicalDevice, 1), &image, 0));
  assert(image_queries == 2);
  for (image_failure = 1; image_failure <= 5; ++image_failure)
    assert(!dpe_gl_image_exportable(fake_image_properties, HANDLE(VkPhysicalDevice, 1), &image, 0));
  image_failure = 0;
  unsetenv("DPE_GL_SEMAPHORE_SYNC");
  assert(dpe_gl_sync_requested());
  setenv("DPE_GL_SEMAPHORE_SYNC", "0", 1);
  assert(!dpe_gl_sync_requested());
  setenv("DPE_GL_SEMAPHORE_SYNC", "1", 1);
  assert(dpe_gl_sync_requested());
  missing_extension = true;
  assert(probe() == -1);
  missing_extension = false;
  mismatch_uuid = true;
  assert(probe() == -1);
  mismatch_uuid = false;
  no_export = true;
  assert(probe() == -1);
  no_export = false;
  assert(probe() == 1);
  no_sync_export = true;
  assert(probe() == -1);
  assert(dpe_gl_sync_probe(get_gl, fake_context, get_instance, HANDLE(VkInstance, 1),
                           HANDLE(VkPhysicalDevice, 1), true) == 1);
  no_sync_export = false;

  fail_import = true;
  assert(!create() && !current);
  assert(fcntl(last_fd, F_GETFD) == -1); // Failed import keeps ownership in the hook.
  fail_import = false;
  assert(create() && import_count == 3);
  frame();
  assert(!strcmp(order, "AWGSE"));
  unsigned first_submissions = submissions;
  frame();
  assert(!strcmp(order, "AWGSEWGSE") && submissions == first_submissions + 1);
  unsigned submitted = submissions, freed = destroyed;
  completed = false;
  assert(dpe_gl_sync_begin(99) == 0 && submissions == submitted);
  // Even an early consumer ACK cannot recycle semaphore payloads still in use.
  dpe_gl_sync_retire();
  assert(!current && retired_count == 1 && destroyed == freed);
  dpe_gl_sync_collect();
  assert(retired_count == 1 && destroyed == freed);
  completed = true;
  dpe_gl_sync_collect();
  assert(!retired && !retired_count && destroyed == freed + 5);

  for (unsigned i = 0; i < MAX_RETIRED; ++i) {
    assert(create());
    frame();
    completed = false;
    dpe_gl_sync_retire();
  }
  assert(retired_count == MAX_RETIRED && !create());
  device_lost = true;
  completed = true;
  dpe_gl_sync_collect();
  assert(retired_count == MAX_RETIRED); // Device loss cannot pretend completion.
  device_lost = false;
  dpe_gl_sync_collect();
  assert(!retired_count);

  assert(create());
  context = (void *)2;
  unsigned deleted = gl_deleted;
  dpe_gl_sync_retire();
  assert(retired_count == 1 && gl_deleted == deleted);
  context = (void *)1;
  dpe_gl_sync_collect();
  assert(!retired_count && gl_deleted == deleted + 2);

  assert(create());
  frame();
  completed = false;
  context = (void *)2;
  deleted = gl_deleted;
  dpe_gl_sync_retire();
  dpe_gl_sync_context_destroyed((void *)1);
  assert(retired_count == 1 && gl_deleted == deleted);
  context = (void *)1; // The new unrelated context reuses the old pointer.
  completed = true;
  dpe_gl_sync_collect();
  assert(!retired_count && gl_deleted == deleted);

  assert(create());
  fail_submit = true;
  submitted = submissions;
  assert(dpe_gl_sync_begin(99) == -1 && submissions == submitted + 1);
  fail_submit = false;
  dpe_gl_sync_retire();
  assert(!retired_count); // No successful submit: immediately reclaimable.

  assert(create());
  assert(dpe_gl_sync_begin(99) == 1);
  fail_export = true;
  assert(dpe_gl_sync_end(99) == -2);
  fail_export = false;
  completed = false;
  dpe_gl_sync_retire();
  assert(retired_count == 1); // Export failed after submission; must keep objects alive.
  completed = true;
  dpe_gl_sync_collect();
  assert(!current && !retired);

  order_count = 0;
  order[0] = 0;
  submitted = submissions;
  assert(create_egl() && dpe_gl_sync_uses_egl());
  frame();
  frame();
  assert(!strcmp(order, "PWGCFEWGCFE") && submissions == submitted);
  completed = false;
  assert(dpe_gl_sync_begin(99) == 0);
  freed = destroyed;
  dpe_gl_sync_retire();
  assert(retired_count == 1 && destroyed == freed);
  device_lost = true;
  completed = true;
  dpe_gl_sync_collect();
  assert(retired_count == 1);
  device_lost = false;
  dpe_gl_sync_collect();
  assert(!retired_count && destroyed == freed + 1);

  assert(create_egl());
  assert(dpe_gl_sync_begin(99) == 1);
  fail_export = true;
  assert(dpe_gl_sync_end(99) == -2);
  fail_export = false;
  completed = false;
  dpe_gl_sync_retire();
  assert(retired_count == 1);
  completed = true;
  dpe_gl_sync_collect();
  assert(!retired_count);

  assert(create_egl());
  assert(dpe_gl_sync_begin(99) == 1);
  fail_egl_create = true;
  assert(dpe_gl_sync_end(99) == -2);
  fail_egl_create = false;
  dpe_gl_sync_retire();
  assert(retired_count == 1); // No fence exists: cannot invent GPU completion.
  dpe_gl_sync_context_destroyed(context);
  assert(!retired_count);
  puts("GL semaphore order, real completion backpressure, fd ownership, bounded retirement passed");
  return 0;
}
