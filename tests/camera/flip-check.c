// Synthetic buffers only; no device access or image persistence.
#include "../../integration/camera/flip.c"
#include <assert.h>
#include <stdio.h>

static int fake_result;
static int copy_frame(const uint8_t *src, size_t length, uint8_t *y, int ys, uint8_t *u, int us,
                      uint8_t *v, int vs, int cx, int cy, int sw, int sh, int cw, int ch,
                      int rotation, uint32_t fourcc) {
  (void)src;
  (void)length;
  (void)cx;
  (void)cy;
  (void)sw;
  (void)sh;
  (void)fourcc;
  if (fake_result)
    return fake_result;
  int w = rotation == 90 || rotation == 270 ? abs(ch) : cw;
  int h = rotation == 90 || rotation == 270 ? cw : abs(ch);
  for (int row = 0; row < h; row++)
    for (int x = 0; x < w; x++)
      y[row * ys + x] = (uint8_t)(row * w + x);
  for (int row = 0; row < (h + 1) / 2; row++)
    for (int x = 0; x < (w + 1) / 2; x++) {
      u[row * us + x] = (uint8_t)(50 + row * w + x);
      v[row * vs + x] = (uint8_t)(100 + row * w + x);
    }
  return 0;
}
int main(int argc, char **argv) {
  original = copy_frame;
  for (int r = 0; r <= 270; r += 90) {
    for (int width = 1; width <= 9; width++) {
      for (int height = 1; height <= 9; height++) {
        uint8_t y[256], u[256], v[256], ey[256], eu[256], ev[256];
        memset(y, 222, sizeof(y));
        memset(u, 222, sizeof(u));
        memset(v, 222, sizeof(v));
        atomic_store(&enabled, false);
        assert(
            !convert_frame(NULL, 0, y, 16, u, 16, v, 16, 0, 0, width, height, width, height, r, 0));
        memcpy(ey, y, sizeof(y));
        memcpy(eu, u, sizeof(u));
        memcpy(ev, v, sizeof(v));
        atomic_store(&enabled, true);
        assert(
            !convert_frame(NULL, 0, y, 16, u, 16, v, 16, 0, 0, width, height, width, height, r, 0));
        int w = r == 90 || r == 270 ? height : width, h = r == 90 || r == 270 ? width : height;
        for (int row = 0; row < 16; row++)
          for (int x = 0; x < 16; x++) {
            assert(y[row * 16 + x] == ey[row * 16 + (row < h && x < w ? w - 1 - x : x)]);
            assert(u[row * 16 + x] ==
                   eu[row * 16 + (row < (h + 1) / 2 && x < (w + 1) / 2 ? (w + 1) / 2 - 1 - x : x)]);
            assert(v[row * 16 + x] ==
                   ev[row * 16 + (row < (h + 1) / 2 && x < (w + 1) / 2 ? (w + 1) / 2 - 1 - x : x)]);
          }
        fake_result = -1;
        memcpy(ey, y, sizeof(y));
        assert(convert_frame(NULL, 0, y, 16, u, 16, v, 16, 0, 0, width, height, width, height, r,
                             0) == -1);
        assert(!memcmp(ey, y, sizeof(y)));
        fake_result = 0;
      }
    }
  }
  if (argc == 3) {
    void *handle = dlopen(argv[1], RTLD_LAZY | RTLD_LOCAL);
    assert(handle);
    struct link_map *map;
    assert(!dlinfo(handle, RTLD_DI_LINKMAP, &map));
    original = (convert_fn)(map->l_addr + strtoul(argv[2], NULL, 0));
    uint8_t src[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}, y[8], u[2], v[2];
    for (int on = 0; on < 2; on++) {
      atomic_store(&enabled, on);
      assert(!convert_frame(src, sizeof(src), y, 4, u, 2, v, 2, 0, 0, 4, 2, 4, 2, 0, 0x30323449));
      assert(y[0] == (on ? 4 : 1) && y[3] == (on ? 1 : 4));
      assert(u[0] == (on ? 10 : 9) && v[0] == (on ? 12 : 11));
    }
    puts("Actual native converter: unchanged and mirrored I420 passed");
  }
  puts("324 synthetic dimensions/rotations, padding preservation and conversion failure passed");
  return 0;
}
