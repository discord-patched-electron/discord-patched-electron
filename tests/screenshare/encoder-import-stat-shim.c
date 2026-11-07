// Test-only fault injection: let the real encoder import a valid GPU buffer,
// while making its cache see an unreported extent. Other callers stay intact.
#define _GNU_SOURCE
#include <assert.h>
#include <dlfcn.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
static int (*original_fstat)(int, struct stat *);
__attribute__((constructor)) static void initialize(void) {
  *(void **)(&original_fstat) = dlsym(RTLD_NEXT, "fstat");
  assert(original_fstat);
}
int fstat(int fd, struct stat *status) {
  int result = original_fstat ? original_fstat(fd, status) : syscall(SYS_fstat, fd, status);
  Dl_info caller;
  if (!result && dladdr(__builtin_return_address(0), &caller) && caller.dli_fname &&
      strstr(caller.dli_fname, "libdpe-hevc.so"))
    status->st_size = 0;
  return result;
}
