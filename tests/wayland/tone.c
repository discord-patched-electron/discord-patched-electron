#include <gtk/gtk.h>
#include <math.h>
#include <pulse/simple.h>
#include <signal.h>
#include <stdint.h>
#include <sys/prctl.h>
#include <unistd.h>
static double freq;
static gpointer tone(gpointer _) {
  pa_sample_spec spec = {PA_SAMPLE_S16LE, 48000, 2};
  int err;
  pa_simple *a = pa_simple_new(NULL, "DPE Wayland tone", PA_STREAM_PLAYBACK, "test-sink",
                               "synthetic tone", &spec, NULL, NULL, &err);
  if (!a)
    return NULL;
  uint64_t sample = 0;
  int16_t b[960];
  for (;;) {
    for (int i = 0; i < 480; i++, sample++)
      b[2 * i] = b[2 * i + 1] = (int16_t)(5000 * sin(2 * M_PI * freq * sample / 48000));
    if (pa_simple_write(a, b, sizeof b, &err) < 0)
      break;
  }
  pa_simple_free(a);
  return NULL;
}
int main(int argc, char **argv) {
  freq = atof(argv[1]);
  // Optional separate audio-owning subprocess exercises application descendants.
  if (argc > 2) {
    pid_t parent = getpid();
    if (fork() == 0) {
      prctl(PR_SET_PDEATHSIG, SIGTERM);
      if (getppid() != parent)
        return 0;
      if (argc > 3) {
        execl("/usr/bin/pw-cat", "pw-cat", "--playback", "--target=test-sink", argv[3], NULL);
        return 5;
      }
      freq = atof(argv[2]);
      tone(NULL);
      return 0;
    }
  }
  gtk_init(NULL, NULL);
  GtkWidget *w = gtk_window_new(GTK_WINDOW_TOPLEVEL);
  gtk_window_set_title(GTK_WINDOW(w), "DPE Wayland test");
  gtk_window_set_default_size(GTK_WINDOW(w), 240, 160);
  gtk_widget_show_all(w);
  if (freq > 0)
    g_thread_new("tone", tone, NULL);
  gtk_main();
}
