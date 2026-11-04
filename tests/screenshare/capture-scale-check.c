#include "../../integration/video/capture-scale.c"
#include <assert.h>
#include <math.h>
#include <stdlib.h>

static uint64_t observed_target;
static struct resolution native_scale(size_t width, size_t height, uint64_t target) {
  observed_target = target;
  if (!((target >> 32) & 255))
    return (struct resolution){width, height};
  if (!width && !height) {
    width = 19200;
    height = 10800;
  }
  unsigned pixels = target;
  if (width * height <= pixels)
    return (struct resolution){width, height};
  double w = sqrt((double)width / height * pixels);
  return (struct resolution){(size_t)w, (size_t)(pixels / w)};
}
extern struct resolution test_scale_call(size_t, size_t, uint64_t, struct sink_wants *, void *);
__asm__(".text\n"
        ".global test_scale_call\n"
        "test_scale_call:\n"
        "push %r15\n"
        "mov %rcx,%r15\n"
        "call *%r8\n"
        "pop %r15\n"
        "ret\n");

int main(void) {
  original_scale = native_scale;
  struct sink_wants wants = {.maximum = INT_MAX, .fps = 60};
  struct resolution result = scaled(1920, 1080, &wants, 0);
  assert(result.width == 1920 && result.height == 1080 && observed_target == 0);
  result = scaled(0, 0, &wants, 0);
  assert(!result.width && !result.height); // Unbounded Source remains native.

  wants.maximum = 1244160; // WebRTC's 3/5 down-adaptation, with no target.
  result = scaled(1920, 1080, &wants, 0);
  assert(result.width == 1486 && result.height == 836);
  assert(result.width * result.height <= (unsigned)wants.maximum);
  assert(observed_target == ((UINT64_C(1) << 32) | 1244160));
  result = scaled(0, 0, &wants, 0);
  assert(result.width == 1486 && result.height == 836);

  wants.has_target = 1;
  wants.target = 2073600; // A target may not override a stricter maximum.
  result = scaled(1920, 1080, &wants, (UINT64_C(1) << 32) | wants.target);
  assert(result.width == 1486 && result.height == 836);
  wants.target = 921600;
  uint64_t native_target = (UINT64_C(1) << 32) | wants.target;
  result = scaled(1920, 1080, &wants, native_target);
  assert(result.width == 1280 && result.height == 720 && observed_target == native_target);

  wants.maximum = wants.target = 921599;
  native_target = (UINT64_C(1) << 32) | wants.target;
  result = scaled(1920, 1080, &wants, native_target);
  assert(result.width == 1278 && result.height == 718 && observed_target == native_target);
  size_t visible_width = (result.width + 1) & ~(size_t)1;
  size_t visible_height = (result.height + 1) & ~(size_t)1;
  assert(visible_width * visible_height <= (unsigned)wants.maximum);

  wants.has_target = 0;
  wants.maximum = INT_MAX;
  result = scaled(1919, 1079, &wants, 0);
  assert(result.width == 1919 && result.height == 1079); // No new padding/crop policy.
  wants.maximum = 2073600;
  result = scaled(1919, 1079, &wants, 0);
  assert(result.width == 1919 && result.height == 1079);
  wants.maximum = 0;
  result = scaled(1920, 1080, &wants, 0);
  assert(result.width == 1920 && result.height == 1080);
  wants.maximum = 921600;
  wants.has_target = 2;
  result = scaled(1920, 1080, &wants, 0);
  assert(result.width == 1920 && result.height == 1080);

  // Test the real emitted dispatch and its SysV struct return. The reviewed
  // call site supplies wants in r15, not an ordinary function argument.
  uint8_t *trampoline =
      mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  assert(trampoline != MAP_FAILED);
  const uint8_t dispatch[] = {0x48, 0x89, 0xd1, 0x4c, 0x89, 0xfa, 0xff, 0x25, 0, 0, 0, 0};
  uintptr_t function = (uintptr_t)scaled;
  memcpy(trampoline, dispatch, sizeof(dispatch));
  memcpy(trampoline + sizeof(dispatch), &function, sizeof(function));
  assert(!mprotect(trampoline, 4096, PROT_READ | PROT_EXEC));
  wants.has_target = 0;
  wants.maximum = 1244160;
  result = test_scale_call(1920, 1080, 0, &wants, trampoline);
  assert(result.width == 1486 && result.height == 836);
  assert(observed_target == ((UINT64_C(1) << 32) | 1244160));
  assert(!munmap(trampoline, 4096));
  puts("GPU pixel limits, optional targets, Source mode and exact register dispatch passed");
  return 0;
}
