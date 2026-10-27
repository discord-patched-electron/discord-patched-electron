// Keep actionable guidance separate from bounded diagnostic output.
module.exports = function failure(error) {
  const details = String(error.message || error).slice(-16000);
  let message =
    "Check your connection and try again. If this keeps happening, reinstall the latest package.";
  let retry = true;
  if (/Python 3 is missing|Brotli is missing|bundled update libraries/.test(details)) {
    message = details
      .split("\n")
      .at(-1)
      .replace(/; the updater will retry\./, ", then try again.");
    retry = false;
  } else if (/Disk quota exceeded|EDQUOT|\[Errno 122\]/i.test(details)) {
    message =
      "Your storage quota was exceeded. Free drive space may still be available. Remove files from the affected filesystem or use a profile location with more quota, then try again.";
    retry = false;
  } else if (/No space left|disk full/i.test(details)) {
    message = "Free up space on the drive containing your Discord profile, then try again.";
    retry = false;
  } else if (/Permission denied|Read-only file system/i.test(details)) {
    message = "Make sure your Discord profile is writable by your user, then try again.";
    retry = false;
  } else if (/First launch requires|reconnect to repair/.test(details)) {
    message =
      "Connect to the internet and launch without DPE_CLIENT_OFFLINE=1 to download or repair Discord.";
    retry = false;
  } else if (
    /Unsupported .*layout|checksum mismatch|Invalid .*|installer database/i.test(details)
  ) {
    message =
      "The update could not be verified or installed. Try again, or reinstall the latest package if this continues.";
    retry = false;
  }
  return { message, details, seconds: retry ? 10 : null };
};
