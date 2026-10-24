// Disposable X11 application with its own PulseAudio playback stream.
#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <math.h>
#include <pulse/error.h>
#include <pulse/simple.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(int argc, char **argv) {
  if (argc != 3)
    return 2;
  Display *display = XOpenDisplay(NULL);
  if (!display)
    return 3;
  Window window = XCreateSimpleWindow(display, DefaultRootWindow(display), 0, 0, 180, 120, 0, 0, 0);
  unsigned long pid = getpid();
  XChangeProperty(display, window, XInternAtom(display, "_NET_WM_PID", False), XA_CARDINAL, 32,
                  PropModeReplace, (unsigned char *)&pid, 1);
  XStoreName(display, window, "DPE synthetic audio application");
  XMapWindow(display, window);
  XFlush(display);
  FILE *info = fopen(argv[2], "w");
  fprintf(info, "{\"pid\":%lu,\"sourceId\":\"window:%lu:0\"}\n", pid, window);
  fclose(info);
  pa_sample_spec spec = {PA_SAMPLE_S16LE, 48000, 2};
  int error;
  pa_simple *audio = pa_simple_new(NULL, "DPE test tone", PA_STREAM_PLAYBACK, "test-sink",
                                   "synthetic tone", &spec, NULL, NULL, &error);
  if (!audio) {
    fprintf(stderr, "%s\n", pa_strerror(error));
    return 4;
  }
  double frequency = atof(argv[1]);
  uint64_t sample = 0;
  int16_t buffer[960];
  for (;;) {
    for (int i = 0; i < 480; i++, sample++)
      buffer[i * 2] = buffer[i * 2 + 1] =
          (int16_t)(5000 * sin(2 * M_PI * frequency * sample / 48000));
    if (pa_simple_write(audio, buffer, sizeof(buffer), &error) < 0)
      break;
  }
  pa_simple_free(audio);
  XCloseDisplay(display);
  return 0;
}
