// SPDX-License-Identifier: MIT
#ifndef DPE_NATIVE_POINTER_H
#define DPE_NATIVE_POINTER_H
#include <stdbool.h>
// Reserved IDs include retired DPE objects. Never send them to an upstream
// object accessor/destructor; active ownership still requires its Rust table.
extern bool dpe_native_pointer_reserved(const void *) __attribute__((weak));
static inline bool dpe_pointer_reserved(const void *value) {
  return dpe_native_pointer_reserved && dpe_native_pointer_reserved(value);
}
#endif
