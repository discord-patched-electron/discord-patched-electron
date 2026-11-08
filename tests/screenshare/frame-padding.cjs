"use strict";

// Test receiver only: describe known VCN4 padding without copying image data.
function av1Padding(image, width, height, hardwareScope) {
  const nativeShortPadding = width % 64 === 0 && height % 16 === 8 && image?.height === height + 2;
  if (
    hardwareScope !== "amd-vcn4" ||
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 128 ||
    height < 128 ||
    width & 1 ||
    height & 1 ||
    width > 8192 ||
    height > 8192 ||
    image?.width !== Math.ceil(width / 64) * 64 ||
    (!nativeShortPadding && image?.height !== Math.ceil(height / 16) * 16) ||
    (image.width === width && image.height === height) ||
    image.data?.length !== image.width * image.height * 4
  )
    return null;
  const x = Math.floor((image.width - width) / 2) & ~1;
  const y = Math.floor((image.height - height) / 2) & ~1;
  let valid = true,
    maxValue = 0,
    maxInterior = 0;
  const check = (row, begin, end) => {
    for (let col = begin; col < end; col++) {
      const offset = (row * image.width + col) * 4;
      const value = Math.max(image.data[offset], image.data[offset + 1], image.data[offset + 2]);
      maxValue = Math.max(maxValue, value);
      // RGB reconstruction can blend one adjacent 4:2:0 chroma sample across
      // a sharp colour edge. Check the remaining black margin independently.
      const dx = col < x ? x - col : col >= x + width ? col - x - width + 1 : 0;
      const dy = row < y ? y - row : row >= y + height ? row - y - height + 1 : 0;
      if (Math.max(dx, dy) > 1) {
        maxInterior = Math.max(maxInterior, value);
        if (value > 40) valid = false;
      }
    }
  };
  for (let row = 0; row < image.height; row++) {
    if (row < y || row >= y + height) check(row, 0, image.width);
    else {
      check(row, 0, x);
      check(row, x + width, image.width);
    }
  }
  return { rect: { x, y, width, height }, valid, maxValue, maxInterior };
}
module.exports = { av1Padding };
