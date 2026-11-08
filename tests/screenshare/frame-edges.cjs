"use strict";

function fullPictureEdges({ width: stride, height: rows, data, contentRect } = {}) {
  const { x: originX = 0, y: originY = 0, width = stride, height = rows } = contentRect || {};
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 64 ||
    height < 64 ||
    !data ||
    data.length !== stride * rows * 4 ||
    ![originX, originY, stride, rows].every(Number.isInteger) ||
    originX < 0 ||
    originY < 0 ||
    originX + width > stride ||
    originY + height > rows
  )
    return false;
  const pixel = (x, y) => {
    const offset = ((originY + y) * stride + originX + x) * 4;
    return Array.from(data.slice(offset, offset + 3));
  };
  const left = pixel(8, Math.floor(height / 2));
  const right = pixel(width - 9, Math.floor(height / 2));
  const bottom = pixel(Math.floor(width / 2), height - 9);
  const last = pixel(Math.floor(width / 2), height - 2);
  return (
    left[0] > 160 &&
    left[1] < 100 &&
    left[2] < 100 &&
    right[1] > 160 &&
    right[0] < 100 &&
    right[2] < 100 &&
    bottom[2] > 160 &&
    bottom[0] < 100 &&
    bottom[1] < 100 &&
    last.every((value) => value > 190)
  );
}

module.exports = { fullPictureEdges };
