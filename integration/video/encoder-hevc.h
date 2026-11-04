#ifndef DPE_ENCODER_HEVC_H
#define DPE_ENCODER_HEVC_H
#include "encoder-abi.h"

bool dpe_hevc_install(const char *filename, const uint32_t offsets[19]);
int dpe_hevc_create(uint32_t type, void *context, void *encoded, void *dropped, void **output,
                    struct encoder_session session, struct encoder_options options);
int dpe_av1_create(uint32_t type, void *context, void *encoded, void *dropped, void **output,
                   struct encoder_session session, struct encoder_options options);
bool dpe_hevc_owned(void *handle);
bool dpe_hevc_active(void);
bool dpe_hevc_update(void *handle, struct encoder_options options);
bool dpe_hevc_destroy(void *handle);
void dpe_hevc_stats(uint64_t result[6]);
void dpe_av1_stats(uint64_t result[6]);
#endif
