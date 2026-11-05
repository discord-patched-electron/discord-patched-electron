const { test } = require("node:test");
const assert = require("node:assert/strict");
const { av1Padding } = require("./frame-padding.cjs");
const { drawMarker, readMarker } = require("./frame-markers.cjs");
const { fullPictureEdges } = require("./frame-edges.cjs");

function source(width, height) {
  const data = new Uint8ClampedArray(width * height * 4);
  const fill = (x, y, w, h, color) => {
    for (let row = y; row < y + h; row++)
      for (let col = x; col < x + w; col++) data.set([...color, 255], (row * width + col) * 4);
  };
  fill(0, 0, width, height, [80, 80, 80]);
  fill(0, 0, 16, height, [230, 0, 0]);
  fill(width - 16, 0, 16, height, [0, 230, 0]);
  fill(0, height - 16, width, 16, [0, 0, 230]);
  fill(0, height - 4, width, 4, [230, 230, 230]);
  const context = {
    fillStyle: "black",
    fillRect(x, y, w, h) {
      fill(x, y, w, h, this.fillStyle === "white" ? [255, 255, 255] : [0, 0, 0]);
    },
  };
  drawMarker(context, width, height, 0x123456);
  return { width, height, data };
}
function padded(input) {
  const width = Math.ceil(input.width / 64) * 64,
    height = Math.ceil(input.height / 16) * 16;
  const data = new Uint8ClampedArray(width * height * 4);
  const x = Math.floor((width - input.width) / 2) & ~1,
    y = Math.floor((height - input.height) / 2) & ~1;
  for (let row = 0; row < input.height; row++)
    data.set(
      input.data.subarray(row * input.width * 4, (row + 1) * input.width * 4),
      ((row + y) * width + x) * 4,
    );
  return { width, height, data };
}
test("known VCN4 padding retains native marker coordinates and every source edge", () => {
  for (const [w, h] of [
    [1920, 1080],
    [1918, 1078],
    [854, 480],
    [1282, 722],
  ]) {
    const image = padded(source(w, h)),
      layout = av1Padding(image, w, h, "amd-vcn4");
    assert.equal(layout.valid, true);
    const sample = { ...image, contentRect: layout.rect };
    assert.equal(readMarker(sample), 0x123456);
    assert.equal(fullPictureEdges(sample), true);
    assert.equal(fullPictureEdges(image), false);
    assert.equal(av1Padding(image, w, h, "unknown"), null);
    assert.equal(av1Padding(image, w, h, "intel"), null);
    assert.equal(av1Padding(image, w - 64, h, "amd-vcn4"), null);
    image.data[image.data.length - 4] = 200;
    assert.equal(av1Padding(image, w, h, "amd-vcn4").valid, false);
  }
});
test("unrequested sizes and out-of-bounds content rectangles cannot validate framing", () => {
  const image = source(1280, 720);
  assert.equal(av1Padding(image, 1280, 720, "amd-vcn4"), null);
  assert.equal(
    readMarker({ ...image, contentRect: { x: 1, y: 0, width: 1280, height: 720 } }),
    null,
  );
  assert.equal(
    fullPictureEdges({ ...image, contentRect: { x: 0, y: -1, width: 1280, height: 720 } }),
    false,
  );
});

test("native VCN4 two-row padding preserves the source origin and black bottom rows", () => {
  for (const [width, height] of [
    [640, 360],
    [1920, 1080],
  ]) {
    const input = source(width, height);
    const image = {
      width,
      height: height + 2,
      data: new Uint8ClampedArray(width * (height + 2) * 4),
    };
    image.data.set(input.data);
    const layout = av1Padding(image, width, height, "amd-vcn4");
    assert.deepEqual(layout.rect, { x: 0, y: 0, width, height });
    assert.equal(layout.valid, true);
    const sample = { ...image, contentRect: layout.rect };
    assert.equal(readMarker(sample), 0x123456);
    assert.equal(fullPictureEdges(sample), true);
    image.data[image.data.length - 4] = 200;
    assert.equal(av1Padding(image, width, height, "amd-vcn4").valid, false);
  }
});
