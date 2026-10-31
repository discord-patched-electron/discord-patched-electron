// Source checkouts resolve the pinned dependency. Packaging replaces this entry
// with its self-contained Bun bundle, so installed clients need no node_modules.
module.exports = require("preact");
