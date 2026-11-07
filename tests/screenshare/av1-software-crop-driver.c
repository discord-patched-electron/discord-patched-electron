// Native fixture and Rust implementation meet at the actual patched call ABI.
#define _GNU_SOURCE
#include "../../integration/video/av1-software-crop.h"
#include <assert.h>
#include <dlfcn.h>
#include <string.h>
int main(int argc, char **argv) {
  assert(argc == 2);
  void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  assert(library);
  void (*scope)(uint32_t *) = dlsym(library, "fixture_scope");
  void (*run)(void (*)(uint64_t *)) = dlsym(library, "fixture_run");
  assert(scope && run);
  uint32_t offsets[10], bad[10];
  scope(offsets);
  memcpy(bad, offsets, sizeof(bad));
  bad[9]++;
  assert(!dpe_software_av1_install(argv[1], bad));
  uint64_t stats[6];
  dpe_software_av1_stats(stats);
  assert(!stats[0] && !stats[1]);
  assert(dpe_software_av1_install(argv[1], offsets));
  assert(dpe_software_av1_install(argv[1], offsets));
  assert(!dpe_software_av1_install(argv[1], bad));
  run(dpe_software_av1_stats);
  dpe_software_av1_stats(stats);
  assert(stats[0] && !stats[1] && stats[2] && stats[3] == 3 && stats[5] == 1);
  dlclose(library);
  return 0;
}
