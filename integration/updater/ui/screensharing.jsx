/** @jsxImportSource preact */
import { useContext, useEffect, useRef, useState } from "preact/hooks";
import {
  Button,
  CheckboxMenu,
  Command,
  Presentation,
  Disclosure,
  Icon,
  Switch,
  errorMessage,
} from "./primitives.jsx";

const permissionReasons = {
  "update-not-approved":
    "The capture helper update could not be verified. Connect to the internet and restart DPE, or update permission manually.",
  "helper-missing": "Direct capture setup is unavailable in this build.",
  "authorization-unavailable":
    "Desktop authorization is unavailable. Check that polkit and an authentication agent are running.",
  "permission-denied":
    "The system rule is installed, but desktop authorization is not allowing direct capture.",
  "permission-blocked": "This launch environment blocks direct capture permission.",
  "authorization-cancelled": "Authorization was cancelled. You can try again.",
  "setup-failed": "Direct capture permission could not be applied. Try again.",
  "setup-unavailable": "Direct capture setup is unavailable in this build.",
  "permission-update-required":
    "The installed permission needs an update before direct capture can be used.",
};
const label = (value, choices) => choices[value] || value || "Not reported";
const rate = (value, unit) =>
  Number.isFinite(value) ? `${Number(value.toFixed(1))} ${unit}` : "Not reported";
const bitrate = (value) =>
  !Number.isFinite(value)
    ? "Not reported"
    : value >= 1000000
      ? rate(value / 1000000, "Mbps")
      : value >= 1000
        ? rate(value / 1000, "kbps")
        : rate(value, "bps");

function CurrentShare({ runtime, loading, error, refresh }) {
  const width = runtime?.dimensions?.width;
  const height = runtime?.dimensions?.height;
  const notStarted = runtime?.encodedFrames === 0 && runtime?.outputFps === 0 && !runtime?.encoder;
  const rows =
    runtime?.state === "active"
      ? [
          [
            "Capture method",
            label(runtime.captureMethod, {
              portal: "System picker (PipeWire)",
              pipewire: "PipeWire",
              x11: "X11 capture",
              "wayland-direct": "Direct Wayland",
              vkcapture: "Vulkan / OpenGL game capture",
              kms: "KMS monitor capture",
            }),
          ],
          [
            "Source",
            label(runtime.sourceType, { screen: "Entire screen", window: "Application window" }),
          ],
          ...(["vkcapture", "kms"].includes(runtime.captureMethod)
            ? [
                [
                  "GPU handoff",
                  label(runtime.captureHandoff, {
                    relay: "Direct buffer sharing",
                    copy: runtime.captureMethod === "kms" ? "GPU snapshot" : "Compatibility copy",
                  }),
                ],
              ]
            : []),
          ...(runtime.captureHandoffFallback
            ? [
                [
                  "Capture fallback",
                  label(runtime.captureHandoffFallback, {
                    "export-unavailable": "Direct GPU allocation unavailable",
                    "synchronization-unavailable": "GPU release synchronization unavailable",
                    "upstream-implicit-sync": "OBS hook compatibility (implicit synchronization)",
                    "cursor-composition": "GPU cursor composition",
                  }),
                ],
              ]
            : []),
          ["Requested frame rate", rate(runtime.requestedFps, "FPS")],
          ["Capture frame rate", rate(runtime.inputFps, "FPS")],
          ["Outgoing frame rate", rate(runtime.outputFps, "FPS")],
          [
            "Capture resolution",
            runtime.sourceDimensions?.width && runtime.sourceDimensions?.height
              ? `${runtime.sourceDimensions.width} × ${runtime.sourceDimensions.height}`
              : "Not reported",
          ],
          ["Outgoing resolution", width && height ? `${width} × ${height}` : "Not reported"],
          ["Outgoing bitrate", bitrate(runtime.bitrate)],
          ["Negotiated codec", runtime.actualCodec || "Not reported"],
          ["Requested codec", runtime.requestedCodec || "Not reported"],
          ["Encoder", runtime.encoder || (notStarted ? "Not started" : "Not reported")],
          [
            "Encoding",
            label(runtime.hardware, {
              hardware: "Hardware",
              software: "Software",
              unknown: notStarted ? "Not started" : "Not reported",
            }),
          ],
          [
            "Encoder fallback",
            runtime.fallbackReason ||
              (runtime.fallbackCount === 0
                ? "None reported"
                : Number.isFinite(runtime.fallbackCount)
                  ? `${runtime.fallbackCount} reported`
                  : "Not reported"),
          ],
        ]
      : [];
  return (
    <section class="settings-group" aria-labelledby="screenshare-current-title" aria-busy={loading}>
      <h3 id="screenshare-current-title" class="section-label">
        Current screenshare
      </h3>
      <p
        id="screenshare-current-state"
        class="hint"
        role="status"
        hidden={Boolean(rows.length) || Boolean(error)}
      >
        {loading && !runtime
          ? "Loading screenshare details…"
          : runtime?.state === "idle"
            ? "You aren’t sharing your screen."
            : "Live details aren’t available. Start a screenshare to check its capture and encoding."}
      </p>
      {rows.length > 0 && (
        <dl id="screenshare-details" class="dpe-details-list">
          {rows.map(([name, value]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {rows.length > 0 && notStarted && (
        <p id="screenshare-encoding-pending" class="hint">
          No frames have been encoded yet. With no viewers, this is expected. The negotiated codec
          does not identify an active hardware or software encoder.
        </p>
      )}
      <p id="screenshare-status-error" role="alert" hidden={!error}>
        {error}
      </p>
      {error && <Button onClick={refresh}>Try again</Button>}
      {runtime?.state === "active" && (runtime.adaptation || runtime.encoding) && (
        <Disclosure id="screenshare-adaptation" label="Encoding details">
          <p class="hint">
            Quality limits can reflect encoder quality or bandwidth. Discard counts are cumulative
            for the reported video stream.
          </p>
          <dl class="dpe-details-list">
            <div>
              <dt>Configured video bitrate limit</dt>
              <dd>{bitrate(runtime.encoding?.configuredMaxBitrate)}</dd>
            </div>
            <div>
              <dt>Connection bandwidth estimate</dt>
              <dd>{bitrate(runtime.encoding?.estimatedConnectionBandwidth)}</dd>
            </div>
            <div>
              <dt>Reported quantizer (codec-specific)</dt>
              <dd>
                {Number.isFinite(runtime.encoding?.averageQp)
                  ? Number(runtime.encoding.averageQp.toFixed(1))
                  : "Not reported"}
              </dd>
            </div>
            <div>
              <dt>Key frames encoded</dt>
              <dd>{runtime.encoding?.keyFramesEncoded ?? "Not reported"}</dd>
            </div>
            {[
              ["cpuLimitedFrameRate", "CPU limited frame rate"],
              ["cpuLimitedResolution", "CPU limited resolution"],
              ["bwLimitedFrameRate", "Quality-limited frame rate"],
              ["bwLimitedResolution", "Quality-limited resolution"],
            ].map(([name, title]) => (
              <div key={name}>
                <dt>{title}</dt>
                <dd>
                  {runtime.adaptation?.[name] === true
                    ? "Yes"
                    : runtime.adaptation?.[name] === false
                      ? "No"
                      : "Not reported"}
                </dd>
              </div>
            ))}
            <div>
              <dt>CPU adjustments</dt>
              <dd>{runtime.adaptation?.cpuChanges ?? "Not reported"}</dd>
            </div>
            <div>
              <dt>Quality adjustments</dt>
              <dd>{runtime.adaptation?.qualityChanges ?? "Not reported"}</dd>
            </div>
            {[
              ["rateLimiterDiscards", "Rate limiter frame discards"],
              ["encoderQueueDiscards", "Encoder queue frame discards"],
              ["congestionWindowDiscards", "Congestion window frame discards"],
            ].map(([name, title]) => (
              <div key={name}>
                <dt>{title}</dt>
                <dd>{runtime.encoding?.[name] ?? "Not reported"}</dd>
              </div>
            ))}
          </dl>
        </Disclosure>
      )}
      {runtime?.state === "active" && runtime.captureCounters && (
        <Disclosure id="screenshare-counters" label="Capture details">
          <dl class="dpe-details-list">
            <div>
              <dt>Frame handling</dt>
              <dd>{runtime.captureCounters.mode || "Not reported"}</dd>
            </div>
            <div>
              <dt>Producer frame-rate limit</dt>
              <dd>{rate(runtime.captureCounters.upstreamFps, "FPS")}</dd>
            </div>
            <div>
              <dt>Frames received</dt>
              <dd>{runtime.captureCounters.frames ?? "Not reported"}</dd>
            </div>
            <div>
              <dt>Older frames skipped</dt>
              <dd>{runtime.captureCounters.dropped ?? "Not reported"}</dd>
            </div>
            <div>
              <dt>Frames above share rate skipped</dt>
              <dd>{runtime.captureCounters.paced ?? "Not reported"}</dd>
            </div>
            <div>
              <dt>Frames with explicit synchronization</dt>
              <dd>{runtime.captureCounters.syncTimelineFrames ?? "Not reported"}</dd>
            </div>
            <div>
              <dt>Invalid synchronization metadata</dt>
              <dd>{runtime.captureCounters.syncTimelineInvalidFrames ?? "Not reported"}</dd>
            </div>
            <div>
              <dt>GPU wait deadline correction</dt>
              <dd>{runtime.captureCounters.syncDeadlineCorrection ? "Active" : "Not reported"}</dd>
            </div>
          </dl>
          <p class="hint">
            Explicit synchronization lets capture wait for the producer’s GPU work. Other streams
            may use implicit synchronization; absence alone does not indicate a problem.
          </p>
        </Disclosure>
      )}
    </section>
  );
}

export function Screensharing({ active, config }) {
  const [audio, setAudio] = useState(() => ({
    audioAutoDetect: config?.audioAutoDetect !== false,
    audioSkipScreenPicker: config?.audioSkipScreenPicker !== false,
  }));
  const [values, setValues] = useState();
  const [runtime, setRuntime] = useState();
  const [permission, setPermission] = useState();
  const { gameCaptureCommand } = useContext(Presentation);
  const [permissionTarget, setPermissionTarget] = useState(null);
  const [experimentalTarget, setExperimentalTarget] = useState(null);
  const [permissionBusy, setPermissionBusy] = useState(false);
  const [permissionError, setPermissionError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [statusError, setStatusError] = useState("");
  const saving = useRef(false);
  const generation = useRef(0);
  const retry = useRef(() => {});
  useEffect(() => {
    if (!active) return;
    const current = ++generation.current;
    let timer;
    let pending = false;
    const update = async (initial = false) => {
      if (pending) return;
      clearTimeout(timer);
      if (current !== generation.current) return;
      pending = true;
      setLoading(true);
      try {
        if (initial) {
          const result = await window.Native.screenshareLoad();
          if (current !== generation.current) return;
          if (!saving.current) setValues(result.values);
          setRuntime(result.runtime);
          setError("");
          try {
            const nextPermission = await window.Native.capturePermissionStatus();
            if (current === generation.current) {
              setPermission(nextPermission);
              setPermissionError("");
            }
          } catch (failure) {
            if (current === generation.current) setPermissionError(errorMessage(failure));
          }
        } else {
          const result = await window.Native.screenshareStatus();
          if (current !== generation.current) return;
          setRuntime(result);
        }
        setStatusError("");
      } catch (failure) {
        if (current !== generation.current) return;
        if (initial) setError(errorMessage(failure));
        else setStatusError(errorMessage(failure));
      } finally {
        pending = false;
        if (current === generation.current) {
          setLoading(false);
          timer = setTimeout(() => {
            if (!document.hidden) void update();
          }, 3000);
        }
      }
    };
    const visible = () => {
      if (!document.hidden) void update();
    };
    retry.current = () => update(!values);
    document.addEventListener("visibilitychange", visible);
    void update(true);
    return () => {
      generation.current++;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [active]);
  const save = async (patch) => {
    if (saving.current || !values) return;
    const focused = document.activeElement;
    saving.current = true;
    setBusy(true);
    setError("");
    const previous = values;
    setValues({ ...values, ...patch });
    try {
      const result = await window.Native.screenshareSave(patch);
      setValues(result.values);
      setError(result.error || "");
      return result.values;
    } catch (failure) {
      setValues(previous);
      setError(errorMessage(failure));
    } finally {
      saving.current = false;
      setBusy(false);
      requestAnimationFrame(() => {
        if (
          document.activeElement === document.body &&
          focused?.isConnected &&
          !focused.disabled &&
          !focused.closest("[hidden]")
        )
          focused.focus({ preventScroll: true });
      });
    }
  };
  const allowCapture = async (enabled) => {
    if (permissionBusy) return false;
    const focused = document.activeElement;
    setPermissionBusy(true);
    setPermissionTarget(enabled);
    setPermissionError("");
    try {
      const result = await (enabled
        ? window.Native.capturePermissionAllow()
        : window.Native.capturePermissionRevoke());
      setPermission(result);
      const complete = enabled
        ? result.allowed === true && !result.needsUpdate
        : result.configured === false;
      if (!complete)
        setPermissionError(permissionReasons[result.reason] || permissionReasons["setup-failed"]);
      return complete;
    } catch (failure) {
      setPermissionError(errorMessage(failure));
      try {
        setPermission(await window.Native.capturePermissionStatus());
      } catch {
        /* Keep the last confirmed grant. */
      }
    } finally {
      setPermissionBusy(false);
      setPermissionTarget(null);
      requestAnimationFrame(() => {
        const target = focused?.isConnected
          ? focused
          : focused?.id === "screenshare-permission-update"
            ? document.getElementById("screenshare-permission-allow")
            : null;
        if (document.activeElement === document.body && target && !target.disabled)
          target.focus({ preventScroll: true });
      });
    }
  };
  const changeExperimental = async (enabled) => {
    if (permissionBusy || busy || experimentalTarget !== null) return;
    setExperimentalTarget(enabled);
    try {
      if (!enabled) {
        setPermissionBusy(true);
        let current;
        try {
          current = await window.Native.capturePermissionStatus();
          setPermission(current);
        } finally {
          setPermissionBusy(false);
        }
        if (current.configured && !(await allowCapture(false))) return;
      }
      const saved = await save({ experimentalCapture: enabled });
      if (enabled && saved?.experimentalCapture && permission?.configured && !permission.allowed)
        await allowCapture(true);
    } catch (failure) {
      setPermissionError(errorMessage(failure));
    } finally {
      setExperimentalTarget(null);
    }
  };
  const experimentalEnabled = experimentalTarget ?? values?.experimentalCapture === true;
  const permissionMessage =
    permissionError ||
    (permission &&
    (permission.available === false || (permission.configured && !permission.allowed))
      ? permissionReasons[permission.reason] || permissionReasons["setup-failed"]
      : "");
  return (
    <section
      id="screensharing-panel"
      role="tabpanel"
      aria-labelledby="t_screensharing"
      hidden={!active}
    >
      <section class="settings-group" aria-labelledby="screenshare-defaults-title">
        <h3 id="screenshare-defaults-title" class="section-label">
          Capture
        </h3>
        <div class="dpe-settings-stack">
          <Switch
            id="screenshare-experimental"
            label="Experimental capture methods"
            experimental
            description="Add optimized screens and game capture to Discord’s screenshare picker."
            checked={experimentalEnabled}
            disabled={!values || busy || permissionBusy || experimentalTarget !== null}
            pending={busy || permissionBusy || experimentalTarget !== null}
            onChange={changeExperimental}
          />
          <div
            class="dpe-capture-launch-help dpe-disabled-content"
            aria-disabled={!experimentalEnabled}
            inert={!experimentalEnabled}
            onCopy={(event) => {
              if (!experimentalEnabled) event.preventDefault();
            }}
            onCut={(event) => {
              if (!experimentalEnabled) event.preventDefault();
            }}
          >
            <h3 class="flags-label">Game capture</h3>
            <p class="hint">
              Optimized game capture requires vkcapture. Launch the game with this wrapper, then
              choose it in Discord screenshare’s Optimized tab. Restart games that are already
              running.{" "}
              <a
                href="https://github.com/discord-patched-electron/discord-patched-electron/blob/main/docs/optimized-screensharing.md"
                target="_blank"
                rel="noreferrer"
              >
                Learn more
              </a>
            </p>
            <Command disabled={!experimentalEnabled || !gameCaptureCommand}>
              {gameCaptureCommand || "Loading game capture command…"}
            </Command>
            <p class="hint">
              Use this in Steam launch options. For other launchers, replace %command% with the
              game’s executable and arguments.
            </p>
          </div>
          <Switch
            id="screenshare-permission-allow"
            label="Allow direct screen capture"
            warning
            description={
              <>
                Direct capture is insecure compared to PipeWire because it bypasses the desktop
                permission picker. Permission is limited to the currently authorized Discord
                executable and its capture workers. Initial approval enables verified helper updates
                without further authentication. Other apps running as your user may still control
                Discord.
                <span
                  id="screenshare-permission-status"
                  class="dpe-setting-feedback"
                  role="status"
                  style={{
                    color:
                      permissionBusy || !permission
                        ? "var(--text-feedback-warning, var(--status-warning, #f0b232))"
                        : permissionMessage || !permission?.configured
                          ? "var(--dpe-danger)"
                          : "var(--dpe-positive)",
                  }}
                >
                  {permissionBusy
                    ? permissionTarget === false || experimentalTarget === false
                      ? "Removing system permission…"
                      : "Waiting for authentication…"
                    : permissionMessage ||
                      (permission
                        ? permission.configured
                          ? "System permission installed"
                          : "System permission uninstalled"
                        : "Checking system permission…")}
                </span>
              </>
            }
            checked={permissionTarget ?? permission?.configured === true}
            pending={permissionBusy || busy}
            disabled={
              (!values?.experimentalCapture && !permission?.configured) ||
              busy ||
              permissionBusy ||
              !permission ||
              (permission.configured
                ? permission.canRevoke === false
                : permission.available === false)
            }
            onChange={allowCapture}
          />
          {values?.experimentalCapture &&
            permission?.configured &&
            (!permission.allowed || permission.needsUpdate) &&
            permission.available && (
              <Button
                id="screenshare-permission-update"
                disabled={busy || permissionBusy}
                onClick={() => allowCapture(true)}
              >
                {permission.allowed ? "Update helper" : "Update permission"}
              </Button>
            )}
          <Switch
            id="screenshare-pacing"
            label="Match capture to the share frame rate"
            description="Skip excess PipeWire frames before processing to reduce capture work. Applies to your next screenshare."
            checked={values?.capturePacing !== false}
            disabled={!values || busy}
            pending={busy}
            onChange={(capturePacing) => save({ capturePacing })}
          />
        </div>
        <p id="screenshare-save-error" role="alert" hidden={!error}>
          {error}
        </p>
        {error && !values && <Button onClick={() => retry.current()}>Try again</Button>}
      </section>
      <section class="settings-group" aria-labelledby="screenshare-audio-title">
        <h3 id="screenshare-audio-title" class="section-label">
          Audio
        </h3>
        {[
          [
            "audioAutoDetect",
            "Automatically detect screen-share audio",
            "On Wayland, select a shared application's audio when its playback can be confidently matched. Otherwise, ask you to choose. Applies to your next share.",
          ],
          [
            "audioSkipScreenPicker",
            "Skip audio picker for entire screens",
            "On Wayland, share system audio automatically when an entire screen is confirmed, excluding Discord. Turn off to choose audio for screens too. Applies to your next share.",
          ],
        ].map(([key, label, description]) => (
          <Switch
            key={key}
            id={`setting-${key}`}
            label={label}
            description={description}
            checked={audio[key]}
            onChange={(checked) => {
              setAudio((current) => ({ ...current, [key]: checked }));
              config[key] = checked;
              window.Native.set(config);
            }}
          />
        ))}
      </section>
      <section class="settings-group" aria-labelledby="screenshare-quality-title">
        <h3 id="screenshare-quality-title" class="section-label">
          Quality
        </h3>
        <Switch
          id="screenshare-preserve-bitrate"
          label="Preserve bitrate in smaller viewer windows"
          description="Keep the bitrate allowance based on your source and selected quality when viewers use smaller tiles. Can use more bandwidth. Applies to your next screenshare."
          checked={values?.preserveViewerBitrate === true}
          disabled={!values || busy}
          pending={busy}
          onChange={(preserveViewerBitrate) => save({ preserveViewerBitrate })}
        />
      </section>
      <section class="settings-group" aria-labelledby="screenshare-codecs-title">
        <h3 id="screenshare-codecs-title" class="section-label">
          Codecs
        </h3>
        <div class="diagnostic-row dpe-action-setting">
          <div>
            <h3>
              <Icon name="experimental" label="Experimental" className="risk-icon danger" />
              Excluded codecs
            </h3>
            <p id="screenshare-codecs-help" class="hint">
              Exclude codecs from your next screenshare’s encoding. Discord chooses a supported
              remaining codec. Camera calls and video playback are unchanged.
            </p>
          </div>
          <CheckboxMenu
            id="screenshare-codecs"
            name="Excluded screenshare codecs"
            descriptionId="screenshare-codecs-help"
            label={
              values?.excludedCodecs?.length
                ? `${values.excludedCodecs.length} excluded`
                : "None excluded"
            }
            disabled={!values}
            options={["AV1", "H265", "H264", "VP8"].map((codec) => ({
              id: codec,
              label: { H265: "H.265 / HEVC", H264: "H.264" }[codec] || codec,
              checked: values?.excludedCodecs?.includes(codec) || false,
              disabled:
                busy ||
                (values?.excludedCodecs?.length === 3 && !values.excludedCodecs.includes(codec)),
            }))}
            onChange={(codec, checked) =>
              save({
                excludedCodecs: checked
                  ? [...(values.excludedCodecs || []), codec]
                  : values.excludedCodecs.filter((name) => name !== codec),
              })
            }
          />
        </div>
        <p id="screenshare-codec-error" role="alert" hidden={!runtime?.codecExclusionError}>
          No supported encoder is available with these codec exclusions. Allow another codec and
          start the share again.
        </p>
      </section>
      {runtime?.watching?.length > 0 && (
        <section class="settings-group" aria-labelledby="screenshare-incoming-title">
          <h3 id="screenshare-incoming-title" class="section-label">
            Incoming video
          </h3>
          {runtime.watching.map((video, index) => (
            <dl class="dpe-details-list" key={index}>
              {runtime.watching.length > 1 && (
                <div>
                  <dt>Stream</dt>
                  <dd>{index + 1}</dd>
                </div>
              )}
              <div>
                <dt>Codec</dt>
                <dd>{video.codec || "Not reported"}</dd>
              </div>
              <div>
                <dt>Decoder</dt>
                <dd>{video.decoder || "Not reported"}</dd>
              </div>
              <div>
                <dt>Decoding</dt>
                <dd>
                  {label(video.hardware, {
                    hardware: "Hardware",
                    software: "Software",
                    unknown: "Not reported",
                  })}
                </dd>
              </div>
              <div>
                <dt>Incoming resolution</dt>
                <dd>
                  {video.width && video.height
                    ? `${video.width} × ${video.height}`
                    : "Not reported"}
                </dd>
              </div>
              <div>
                <dt>Decoded frame rate</dt>
                <dd>{rate(video.fps, "FPS")}</dd>
              </div>
            </dl>
          ))}
        </section>
      )}
      <CurrentShare
        runtime={runtime}
        loading={loading}
        error={statusError}
        refresh={() => retry.current()}
      />
    </section>
  );
}
