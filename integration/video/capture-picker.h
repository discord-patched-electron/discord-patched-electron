// SPDX-License-Identifier: MIT
#ifndef DPE_CAPTURE_PICKER_H
#define DPE_CAPTURE_PICKER_H
#include "capture-delivery.h"
struct dpe_picker_native {
  show_fn show;
  new_fn new_capture;
  destroy_fn destroy;
  update_fn update;
  struct dpe_delivery_functions frames;
  uintptr_t selected_mutex, selected_config, selected_present;
  bool (*notify)(const char *, uint32_t, uint32_t);
};
struct dpe_picker_snapshot {
  uint32_t state;
  int32_t fd;
  struct delivery_probe *delivery;
  bool delivery_only;
};
bool dpe_picker_configure(struct dpe_picker_native);
void dpe_picker_enable(bool);
bool dpe_picker_idle(void);
uint32_t dpe_picker_current(void);
bool dpe_picker_inspect(uint32_t, struct dpe_picker_snapshot *);
void dpe_picker_cleanup(void);
int32_t dpe_picker_show(const struct picker_options *);
int32_t dpe_picker_new(const struct capture_options *, void **);
int32_t dpe_picker_update(void *, const struct capture_options *);
int32_t dpe_picker_destroy(void *);
bool dpe_picker_finish(uint32_t, const char *, const char *, uint32_t);
bool dpe_picker_cancel(uint32_t);
#endif
