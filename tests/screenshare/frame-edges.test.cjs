"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { fullPictureEdges } = require("./frame-edges.cjs");

test("full-picture framing rejects bottom cropping and padded rows", () => {
  const width = 128,
    height = 128,
    data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const color =
        y >= height - 4
          ? [255, 255, 255]
          : y >= height - 24
            ? [0, 0, 255]
            : x < 24
              ? [255, 0, 0]
              : x >= width - 24
                ? [0, 255, 0]
                : [64, 64, 64];
      data.set([...color, 255], (y * width + x) * 4);
    }
  }
  assert.equal(fullPictureEdges({ width, height, data }), true);
  assert.equal(
    fullPictureEdges({ width, height: height - 8, data: data.slice(0, width * (height - 8) * 4) }),
    false,
  );
  const padded = new Uint8ClampedArray(width * (height + 8) * 4);
  padded.set(data);
  assert.equal(fullPictureEdges({ width, height: height + 8, data: padded }), false);
  assert.equal(fullPictureEdges(), false);
});
