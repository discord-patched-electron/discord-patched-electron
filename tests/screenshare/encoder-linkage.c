// Deliberately keep dynamic, lazy PLT calls to independently test linkage
// installation without loading Discord, Electron, PipeWire or GPU drivers.
int cc_encoder_create(void) { return 1; }
int cc_encoder_update(void) { return 1; }
void cc_encoder_destroy(void) {}
int cc_encoder_vendor(void) { return 1; }
int make_encoder_calls(void) {
  int result = cc_encoder_create() + cc_encoder_update();
  cc_encoder_destroy();
  return result;
}
