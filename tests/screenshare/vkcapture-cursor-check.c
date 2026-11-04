// SPDX-License-Identifier: MIT
// Synthetic XFixes payload and optional GPU sprite composition, never a real cursor.
#include "../../integration/video/vkcapture-gpu.c"
#include <assert.h>
int main(int argc, char **argv) {
  uint8_t data[DPE_CURSOR_META_SIZE] = {0};
  struct {
    xcb_xfixes_get_cursor_image_reply_t image;
    uint32_t pixels[4];
  } image = {
      .image = {.width = 2, .height = 2, .length = 4, .xhot = 1, .yhot = 1, .cursor_serial = 7},
      .pixels = {0xffff0000, 0xffff0000, 0xff00ff00, 0xff00ff00}};
  xcb_query_pointer_reply_t pointer = {.same_screen = 1, .win_x = 5, .win_y = 6};
  xcb_get_geometry_reply_t geometry = {.width = 16, .height = 16};
  assert(dpe_cursor_encode(data, &pointer, &geometry, &image.image, 16, 16));
  struct spa_meta_cursor *meta = (void *)data;
  assert(meta->position.x == 5 && meta->position.y == 6 && meta->hotspot.x == 1 && meta->id == 7);
  pointer.win_x = -1;
  assert(!dpe_cursor_encode(data, &pointer, &geometry, &image.image, 16, 16));
  pointer.win_x = 5;
  image.image.width = 257;
  assert(!dpe_cursor_encode(data, &pointer, &geometry, &image.image, 16, 16));
  image.image.width = 2;
  assert(!dpe_cursor_encode(data, &pointer, &geometry, &image.image, 32, 16));
  image.image.length = 0;
  assert(!dpe_cursor_encode(data, &pointer, &geometry, &image.image, 16, 16));
  if (argc < 2)
    return 0;
  struct dpe_vk_gpu *gpu = dpe_vk_gpu_create(argv[1], NULL, 16, 16);
  assert(gpu);
  GLuint texture, fbo;
  glGenTextures(1, &texture);
  glBindTexture(GL_TEXTURE_2D, texture);
  glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 16, 16, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
  glGenFramebuffers(1, &fbo);
  glBindFramebuffer(GL_FRAMEBUFFER, fbo);
  glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
  glViewport(0, 0, 16, 16);
  glClearColor(0, 0, 1, 1);
  glClear(GL_COLOR_BUFFER_BIT);
  memcpy(gpu->cursor.metadata, data, sizeof(data));
  glUseProgram(gpu->program);
  draw_cursor_image(gpu, 16, 16);
  uint8_t pixels[16 * 16 * 4];
  glReadPixels(0, 0, 16, 16, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
  // Exported DMA-BUF top-left sprite=(4,5) maps directly to storage rows.
  for (int y = 0; y < 16; y++)
    for (int x = 0; x < 16; x++) {
      unsigned offset = (y * 16 + x) * 4;
      bool red = x >= 4 && x < 6 && y == 5;
      bool green = x >= 4 && x < 6 && y == 6;
      assert(pixels[offset] == (red ? 255 : 0));
      assert(pixels[offset + 1] == (green ? 255 : 0));
      assert(pixels[offset + 2] == (red || green ? 0 : 255));
    }
  memset(gpu->cursor.metadata, 0, sizeof(struct spa_meta_cursor));
  glClear(GL_COLOR_BUFFER_BIT);
  draw_cursor_image(gpu, 16, 16);
  glReadPixels(0, 0, 16, 16, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
  for (int i = 0; i < 16 * 16; i++)
    assert(pixels[i * 4] == 0 && pixels[i * 4 + 2] == 255);
  glDeleteTextures(1, &texture);
  glDeleteFramebuffers(1, &fbo);
  dpe_vk_gpu_destroy(gpu);
  return 0;
}
