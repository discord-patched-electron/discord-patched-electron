// SPDX-License-Identifier: GPL-3.0-only
#ifndef DPE_KMS_CLIENT_H
#define DPE_KMS_CLIENT_H
#include "kms-protocol.h"
#include <sys/types.h>
#define DPE_KMS_OUTPUTS 32
// Advertised by the handshake before a client sends this DPE-only request.
// Its final word is a connector ID; ordinary GSR GET_KMS remains unchanged.
#define DPE_KMS_SCOPE_CAPABILITY "DPE-KMS-SCOPE-1"
#define DPE_KMS_SCOPE_REQUEST 0x44504501u
struct dpe_kms_output {
  char id[64], name[64], card[64], render[64];
  uint32_t connector, crtc, width, height;
  bool supported;
};
struct dpe_kms_client {
  int fd;
  pid_t pid;
  const char *error;
  uintptr_t owner;
  uint32_t connector;
};
struct dpe_kms_scene {
  const gsr_kms_response_item *primary, *cursor;
};
struct dpe_kms_layout {
  unsigned planes;
  bool primary_needs_opaque, primary_present, cursor_present;
};
struct dpe_kms_inspector;
enum dpe_kms_scene_result { DPE_KMS_UNSUPPORTED = -1, DPE_KMS_CHANGED, DPE_KMS_VALID };
// Read-only inventory. Never spawns the privileged helper or obtains pixels.
const char *dpe_kms_helper_status(void);
bool dpe_kms_authorization_required(void);
unsigned dpe_kms_inventory(struct dpe_kms_output[DPE_KMS_OUTPUTS]);
struct dpe_kms_inspector *dpe_kms_inspector_create(int, const struct dpe_kms_output *);
void dpe_kms_inspector_destroy(struct dpe_kms_inspector *);
bool dpe_kms_metadata(struct dpe_kms_inspector *, struct dpe_kms_layout *);
enum dpe_kms_scene_result dpe_kms_scene(const gsr_kms_response *, const struct dpe_kms_output *,
                                        const struct dpe_kms_layout *, struct dpe_kms_scene *);
bool dpe_kms_client_open(struct dpe_kms_client *, const char *, bool authorize);
bool dpe_kms_client_open_output(struct dpe_kms_client *, const struct dpe_kms_output *, bool);
void dpe_kms_client_close(struct dpe_kms_client *);
bool dpe_kms_request(struct dpe_kms_client *);
// Rejected GETFB2 access is distinct from a malformed/unsupported response.
#define DPE_KMS_PERMISSION_DENIED (-2)
int dpe_kms_receive(int, gsr_kms_response *);
void dpe_kms_response_close(gsr_kms_response *);
#endif
