// Runs only inside a new video popout. Read dimensions, never pixels or frames.
// Bounded event-driven discovery handles Discord's delayed React portal mount.
module.exports = function measurePopout() {
  return new Promise((resolve) => {
    let finished = false,
      debounce;
    const videos = new Set();
    const finish = (result) => {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      clearTimeout(debounce);
      observer.disconnect();
      for (const video of videos) {
        video.removeEventListener("loadedmetadata", changed);
        video.removeEventListener("resize", changed);
      }
      resolve(result);
    };
    const measure = () => {
      const candidates = [...document.querySelectorAll("video")]
        .map((video) => {
          const rect = video.getBoundingClientRect();
          return { video, rect, area: rect.width * rect.height };
        })
        .filter(
          ({ video, rect, area }) =>
            video.videoWidth > 0 &&
            video.videoHeight > 0 &&
            area > 0 &&
            rect.right > 0 &&
            rect.bottom > 0 &&
            rect.left < innerWidth &&
            rect.top < innerHeight,
        )
        .sort((a, b) => b.area - a.area);
      if (!candidates.length || (candidates[1] && candidates[0].area < candidates[1].area * 1.5))
        return;
      const { video, rect } = candidates[0];
      let viewport = rect;
      // A centered, aspect-fitted video can be smaller than its layout slot.
      // Those letterbox bands disappear after resizing; they are not chrome.
      for (
        let parent = video.parentElement, depth = 0;
        parent && depth < 4;
        parent = parent.parentElement, depth++
      ) {
        if (parent.querySelectorAll("video").length !== 1) break;
        const slot = parent.getBoundingClientRect();
        const factor = Math.min(slot.width / video.videoWidth, slot.height / video.videoHeight);
        if (
          slot.width <= 0 ||
          slot.height <= 0 ||
          slot.width > innerWidth + 2 ||
          slot.height > innerHeight + 2 ||
          Math.abs(rect.width - video.videoWidth * factor) > 2 ||
          Math.abs(rect.height - video.videoHeight * factor) > 2 ||
          Math.abs(rect.left + rect.width / 2 - slot.left - slot.width / 2) > 2 ||
          Math.abs(rect.top + rect.height / 2 - slot.top - slot.height / 2) > 2
        )
          break;
        viewport = slot;
      }
      finish({
        width: video.videoWidth,
        height: video.videoHeight,
        chromeWidth: Math.max(0, innerWidth - viewport.width),
        chromeHeight: Math.max(0, innerHeight - viewport.height),
      });
    };
    function changed() {
      if (finished) return;
      for (const video of document.querySelectorAll("video")) {
        if (videos.has(video)) continue;
        videos.add(video);
        video.addEventListener("loadedmetadata", changed);
        video.addEventListener("resize", changed);
      }
      clearTimeout(debounce);
      debounce = setTimeout(measure, 150);
    }
    const observer = new MutationObserver(changed);
    const timeout = setTimeout(() => finish(null), 10000);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    changed();
  });
};
