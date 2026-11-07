// SPDX-License-Identifier: MIT
#include "../../integration/video/vkcapture-compat.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
  struct dpe_obs_texture texture = {.type = 11,
                                    .nfd = 1,
                                    .width = 1920,
                                    .height = 1080,
                                    .format = 0x34325241,
                                    .strides = {7680},
                                    .flip = 1};
  struct dpe_vk_packet packet;
  assert(dpe_obs_texture_packet(&texture, 1, 7, &packet));
  assert(packet.generation == 7 && packet.width == 1920 && packet.height == 1080);
  assert(packet.strides[0] == 7680 && packet.flags == DPE_VK_FLIP && packet.nfd == 1);
  assert(!dpe_obs_texture_packet(&texture, 0, 7, &packet));
  assert(!dpe_obs_texture_packet(&texture, 2, 7, &packet));
  assert(!dpe_obs_texture_packet(&texture, 1, 0, &packet));
  texture.strides[0] = -1;
  assert(!dpe_obs_texture_packet(&texture, 1, 7, &packet));
  texture.strides[0] = 7680;
  texture.offsets[0] = -1;
  assert(!dpe_obs_texture_packet(&texture, 1, 7, &packet));
  texture.offsets[0] = 0;
  texture.color_space = 1000104008; // HDR cannot silently be interpreted as SDR.
  assert(!dpe_obs_texture_packet(&texture, 1, 7, &packet));
  texture.color_space = 0;
  texture.width = 9000;
  assert(!dpe_obs_texture_packet(&texture, 1, 7, &packet));
  puts("upstream texture ABI, descriptor bounds and unsupported color space checked");
  return 0;
}
