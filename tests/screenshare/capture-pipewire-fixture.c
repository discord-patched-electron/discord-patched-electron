// Expose only the optional stream APIs used to select the capture queue policy.
// All test frames are supersedable; calling either control path is a failure.
#define _GNU_SOURCE
#include <pipewire/stream.h>
#include <stdlib.h>

int pw_stream_set_error(struct pw_stream *stream, int result, const char *message, ...) {
  (void)stream;
  (void)result;
  (void)message;
  abort();
}

#ifdef DPE_RETURN_BUFFER
int pw_stream_return_buffer(struct pw_stream *stream, struct pw_buffer *buffer) {
  (void)stream;
  (void)buffer;
  abort();
}
#endif
