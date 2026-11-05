// Synthetic PipeWire ownership/queue fixtures; no daemon, devices or captured pixels.
#define _GNU_SOURCE
#include <stdint.h>
static uint64_t fake_time;
#define DPE_CAPTURE_TIME_NS() fake_time
#include "../../integration/video/capture.c"
#include <assert.h>

static struct pw_buffer frames[CAPTURE_LIMIT + 2];
static struct spa_buffer spa_frames[CAPTURE_LIMIT + 2];
static struct spa_data datas[CAPTURE_LIMIT + 2];
static struct spa_chunk chunks[CAPTURE_LIMIT + 2];
static struct spa_meta metas[CAPTURE_LIMIT + 2];
static struct spa_meta_header headers[CAPTURE_LIMIT + 2];
static struct captured_buffer queue[CAPTURE_LIMIT + 2];
static size_t queued, released, returned, errors;
static int return_failure;
static struct pw_stream *stream = (void *)1;

static struct captured_buffer fake_dequeue(struct pw_stream *value) {
  assert(value == stream);
  if (!queued)
    return (struct captured_buffer){NULL, value};
  struct captured_buffer frame = queue[0];
  memmove(queue, queue + 1, (--queued) * sizeof(*queue));
  return frame;
}
static void fake_release(struct captured_buffer *buffer) {
  assert(buffer->stream == stream);
  assert(supersedable(buffer->buffer));
  released++;
}
static int fake_return(struct pw_stream *value, struct pw_buffer *buffer) {
  assert(value == stream);
  returned++;
  if (return_failure)
    return -EINVAL;
  memmove(queue + 1, queue, queued++ * sizeof(*queue));
  queue[0] = (struct captured_buffer){buffer, value};
  return 0;
}
static int fake_error(struct pw_stream *value, int error, const char *format, ...) {
  (void)format;
  assert(value == stream && error == -EIO);
  errors++;
  return 0;
}
static void reset(size_t size) {
  memset(&counts, 0, sizeof(counts));
  queued = size;
  released = returned = errors = 0;
  return_failure = 0;
  original_dequeue = fake_dequeue;
  original_release = fake_release;
  return_buffer = fake_return;
  stream_error = fake_error;
  atomic_store(&optimize, true);
  configure_pacing(0);
  for (size_t i = 0; i < size; i++) {
    headers[i] = (struct spa_meta_header){.pts = -1, .seq = i};
    metas[i] = (struct spa_meta){SPA_META_Header, sizeof(headers[i]), &headers[i]};
    chunks[i] = (struct spa_chunk){.size = 256, .stride = 16};
    datas[i] =
        (struct spa_data){.type = i % 2 ? SPA_DATA_DmaBuf : SPA_DATA_MemFd, .chunk = &chunks[i]};
    spa_frames[i] = (struct spa_buffer){1, 1, &metas[i], &datas[i]};
    frames[i] = (struct pw_buffer){.buffer = &spa_frames[i]};
    queue[i] = (struct captured_buffer){&frames[i], stream};
  }
}
int main(void) {
  // Diagnostics must leave malformed/unfamiliar buffers to native without
  // dereferencing a missing or unbounded metadata inventory.
  for (unsigned count = 1; count <= 65; count += 64) {
    reset(1);
    spa_frames[0].metas = NULL;
    spa_frames[0].n_metas = count;
    struct captured_buffer unchanged = capture_dequeue(stream);
    assert(unchanged.buffer == &frames[0] && !released && !returned);
    assert(counts.pts_unknown == 1 && counts.conservative == 1);
  }
  reset(1);
  spa_frames[0].n_metas = 65;
  assert(capture_dequeue(stream).buffer == &frames[0]);
  assert(!released && !returned && counts.pts_unknown == 1 && counts.conservative == 1);
  // Explicit synchronization is observed only with complete metadata and two
  // valid SyncObj FDs. Neither this inspection nor pacing consumes the points.
  reset(0);
  uint64_t timeline[3] = {0, 17, 18};
  struct spa_meta sync_meta = {9, sizeof(timeline), timeline};
  struct spa_data sync_data[3] = {
      {.type = SPA_DATA_DmaBuf}, {.type = 5, .fd = 12}, {.type = 5, .fd = 13}};
  struct spa_buffer sync_buffer = {1, 3, &sync_meta, sync_data};
  observe_sync(&sync_buffer);
  assert(counts.sync_timeline == 1 && !counts.sync_invalid && !counts.sync_absent);
  sync_data[2].fd = -1;
  observe_sync(&sync_buffer);
  sync_data[2].fd = 13;
  sync_buffer.n_datas = 2;
  observe_sync(&sync_buffer);
  sync_buffer.n_datas = 3;
  sync_data[2].type = SPA_DATA_DmaBuf;
  observe_sync(&sync_buffer);
  sync_data[2].type = 5;
  sync_meta.size = 23;
  observe_sync(&sync_buffer);
  sync_meta.size = sizeof(timeline);
  sync_meta.data = NULL;
  observe_sync(&sync_buffer);
  assert(counts.sync_invalid == 5 && counts.sync_timeline == 1);
  sync_buffer.n_metas = 0;
  observe_sync(&sync_buffer);
  assert(counts.sync_absent == 1);
  sync_data[0].type = SPA_DATA_MemFd;
  observe_sync(&sync_buffer);
  assert(counts.sync_absent == 1 && timeline[1] == 17 && timeline[2] == 18);

  reset(4);
  struct captured_buffer result = capture_dequeue(stream);
  assert(result.buffer == &frames[3] && !queued && released == 3 && returned == 0);
  assert(counts.skipped == 3 && counts.dmabuf == 1 && counts.pts_unknown == 1);
  capture_release(&result);
  assert(released == 4 && processing_buffer == NULL);

  // Busy metadata uses the original release operation's ownership accounting.
  reset(4);
  struct spa_meta_busy busy = {0};
  metas[1] = (struct spa_meta){SPA_META_Busy, sizeof(busy), &busy};
  result = capture_dequeue(stream);
  assert(result.buffer == &frames[3] && released == 3 && !returned);

  // Zero DMA-BUF sizes are valid in PipeWire, but not consumable by the reviewed
  // native callback. Preserve older usable frames rather than selecting one.
  reset(4);
  for (int i = 0; i < 4; i++) {
    datas[i].type = SPA_DATA_DmaBuf;
  }
  chunks[3].size = 0;
  result = capture_dequeue(stream);
  assert(result.buffer == &frames[0] && released == 0 && returned == 3 && counts.empty == 0);
  for (int i = 1; i < 4; i++)
    assert(fake_dequeue(stream).buffer == &frames[i]);
  reset(1);
  datas[0].type = SPA_DATA_DmaBuf;
  chunks[0].size = 0;
  result = capture_dequeue(stream);
  assert(result.buffer == &frames[0] && counts.empty == 0);

  // Observation mode leaves the actual native queue untouched.
  reset(4);
  atomic_store(&optimize, false);
  result = capture_dequeue(stream);
  assert(result.buffer == &frames[0] && queued == 3 && !released && !returned);

  // Missing optional API leaves FIFO behavior, even if latest was requested.
  reset(4);
  return_buffer = NULL;
  result = capture_dequeue(stream);
  assert(result.buffer == &frames[0] && queued == 3 && !released && !returned);

  // Metadata/control frames must reach native in the original order. This also
  // covers SyncTimeline without requiring newer build-time PipeWire headers.
  for (int kind = 0; kind < 7; kind++) {
    reset(4);
    switch (kind) {
    case 0:
      metas[1].type = SPA_META_Cursor;
      break;
    case 1:
      metas[1].type = UINT32_MAX;
      break;
    case 2:
      headers[1].flags = SPA_META_HEADER_FLAG_DISCONT;
      break;
    case 3:
      datas[1].type = SPA_DATA_MemFd;
      chunks[1].size = 0;
      break;
    case 4:
      chunks[1].flags = SPA_CHUNK_FLAG_CORRUPTED;
      break;
    case 5:
      metas[1].size = 1;
      break;
    case 6:
      datas[1].type = SPA_DATA_MemPtr;
      break;
    }
    result = capture_dequeue(stream);
    assert(result.buffer == &frames[0] && queued == 3 && !released && returned == 3);
    for (int i = 1; i < 4; i++)
      assert(fake_dequeue(stream).buffer == &frames[i]);
  }

  // A bounded scan must not silently reorder or skip a longer queue.
  reset(CAPTURE_LIMIT + 1);
  result = capture_dequeue(stream);
  assert(result.buffer == &frames[0] && queued == CAPTURE_LIMIT && released == 0);
  for (int i = 1; i <= CAPTURE_LIMIT; i++)
    assert(fake_dequeue(stream).buffer == &frames[i]);

  reset(3);
  metas[1].type = SPA_META_Cursor;
  return_failure = 1;
  result = capture_dequeue(stream);
  assert(result.buffer == &frames[0] && errors == 2 && counts.return_errors == 2);
  assert(!atomic_load(&optimize) && !released);

  reset(0);
  assert(!capture_dequeue(stream).buffer && counts.frames == 0 && counts.callbacks == 1);

  // Full frames are returned exactly once before any native conversion. Neither
  // a producer at 240 Hz nor repeated callbacks at one instant create credits.
  for (unsigned fps = 30; fps <= 60; fps += 30) {
    reset(1);
    configure_pacing(fps);
    for (unsigned i = 0; i < 240; i++) {
      fake_time = (uint64_t)i * 1000000000ull / 240;
      queue[0] = (struct captured_buffer){&frames[0], stream};
      queued = 1;
      result = capture_dequeue(stream);
      if (result.buffer)
        capture_release(&result);
    }
    assert(counts.frames == fps && counts.paced == 240 - fps && released == 240);
    assert(!frame_due(stream, atomic_load(&pacing_config), fake_time));
  }

  // Small early/late scheduling jitter must preserve a source at the cap.
  for (unsigned fps = 5; fps <= 60; fps += 5) {
    configure_pacing(0);
    configure_pacing(fps);
    for (unsigned i = 0; i < 120; i++) {
      uint64_t now = 1000000000ull + (uint64_t)i * 1000000000ull / fps;
      now += (i % 3) * 200000ull;
      assert(frame_due(stream, atomic_load(&pacing_config), now));
      assert(!frame_due(stream, atomic_load(&pacing_config), now));
    }
  }
  // Stop/restart, a new stream, backwards clock and dynamic FPS reset phase.
  configure_pacing(0);
  configure_pacing(30);
  assert(frame_due(stream, atomic_load(&pacing_config), 9000000000ull));
  configure_pacing(60);
  assert(frame_due(stream, atomic_load(&pacing_config), 9000000001ull));
  assert(frame_due((void *)2, atomic_load(&pacing_config), 9000000002ull));
  assert(frame_due((void *)2, atomic_load(&pacing_config), 1));

  // Cursor/sync/control metadata bypass pacing, even between frame deadlines.
  reset(1);
  configure_pacing(30);
  assert(frame_due(stream, atomic_load(&pacing_config), 100));
  fake_time = 101;
  metas[0].type = SPA_META_Cursor;
  result = capture_dequeue(stream);
  assert(result.buffer == &frames[0] && !released && counts.pacing_bypass == 1);
  puts("Capture ownership: latest frame, FIFO restoration, cursor/sync conservatism, bounds and "
       "failures passed");
  return 0;
}
