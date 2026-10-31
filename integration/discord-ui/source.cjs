"use strict";
const runtime = require("./runtime.cjs");
// All native UI requests initialize/reuse the same page-world runtime. Adapters
// receive capabilities, never a Node bridge or an unrestricted IPC API.
function call(adapter, request, discover) {
  return `(${adapter.toString()})(${JSON.stringify(request)},(${runtime.toString()})()${discover ? `,${discover.toString()}` : ""})`;
}
module.exports = { call };
