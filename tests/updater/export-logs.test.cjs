const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const fsSync = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const { execFileSync } = require("node:child_process");

const directory = path.resolve(
  process.env.DPE_TEST_EXPORT_HELPER_ROOT || path.join(__dirname, "../../integration/updater"),
);
async function fixture(t, { fileOnly = false, gpu = async () => ({}) } = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "dpe-export-test-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const profile = path.join(root, "profile");
  await fs.mkdir(path.join(profile, "logs"), { recursive: true });
  await fs.writeFile(
    path.join(profile, "logs/renderer.log"),
    "Authorization: Bearer EXAMPLE_SECRET\nRenderer failed to decode video",
  );
  const destination = path.join(root, "chosen.zip");
  const electron = {
    app: {
      getPath: (name) => (name === "downloads" ? root : path.join(profile, "Crashpad")),
      getGPUInfo: gpu,
      getAppMetrics: () => [],
      getGPUFeatureStatus: () => ({}),
    },
    dialog: { showSaveDialog: async () => ({ canceled: false, filePath: destination }) },
  };
  const module = { exports: {} };
  const fileSystem = {
    ...fs,
    mkdtemp: (prefix) => {
      if (fileOnly && prefix.startsWith(root + path.sep))
        return Promise.reject(
          Object.assign(new Error("File-only portal grant"), { code: "EACCES" }),
        );
      return fs.mkdtemp(prefix);
    },
  };
  const env = { ...process.env, DPE_PROFILE_DIR: profile };
  vm.runInNewContext(fsSync.readFileSync(path.join(directory, "export-logs.cjs"), "utf8"), {
    module,
    __dirname: directory,
    process: {
      env,
      versions: process.versions,
      platform: process.platform,
      arch: process.arch,
      execPath: process.execPath,
    },
    setTimeout,
    clearTimeout,
    require(name) {
      if (name === "electron") return electron;
      if (name === "node:fs/promises") return fileSystem;
      if (name === "./presentation.cjs") return { metadata: () => ({ discord: "fixture" }) };
      return require(name);
    },
  });
  return { root, profile, destination, electron, env, ...module.exports };
}

function verify(destination) {
  execFileSync("python3", [
    "-c",
    `import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    data = archive.read('profile/logs/renderer.log').decode()
    assert 'EXAMPLE_SECRET' not in data
    assert 'Renderer failed to decode video' in data
    assert json.loads(archive.read('manifest.json'))['versions']['discord'] == 'fixture'
`,
    destination,
  ]);
}

test("a GPU query that never resolves cannot prevent saving the redacted ZIP", async (t) => {
  const f = await fixture(t, { gpu: () => new Promise(() => {}) });
  const result = await f.exportLogs({});
  assert.equal(result.filePath, f.destination);
  verify(f.destination);
  assert.equal((await fs.stat(f.destination)).mode & 0o777, 0o600);
  assert.deepEqual((await fs.readdir(f.root)).sort(), ["chosen.zip", "profile"]);
});

test("file-only save grants work without permission to create sibling directories", async (t) => {
  const f = await fixture(t, { fileOnly: true });
  await f.exportLogs({});
  verify(f.destination);
});

test("failed writes report an error, clean up and allow retry", async (t) => {
  const f = await fixture(t);
  f.electron.dialog.showSaveDialog = async () => ({
    canceled: false,
    filePath: path.join(f.root, "missing/chosen.zip"),
  });
  await assert.rejects(f.exportLogs({}), /ENOENT/);
  f.electron.dialog.showSaveDialog = async () => ({ canceled: false, filePath: f.destination });
  await f.exportLogs({});
  verify(f.destination);
  assert.deepEqual((await fs.readdir(f.root)).sort(), ["chosen.zip", "profile"]);
});

test("a missing profile still saves available system evidence and a manifest", async (t) => {
  const f = await fixture(t, {
    gpu: async () => {
      throw new Error("GPU unavailable");
    },
  });
  f.env.DPE_PROFILE_DIR = path.join(f.root, "missing-profile");
  await f.exportLogs({});
  execFileSync("python3", [
    "-c",
    "import sys, zipfile; assert 'manifest.json' in zipfile.ZipFile(sys.argv[1]).namelist()",
    f.destination,
  ]);
});

test("worker startup failure preserves an existing destination and releases the export lock", async (t) => {
  const f = await fixture(t);
  await fs.writeFile(f.destination, "previous archive");
  const originalPath = f.env.PATH;
  f.env.PATH = "";
  await assert.rejects(f.exportLogs({}), /ENOENT/);
  assert.equal(await fs.readFile(f.destination, "utf8"), "previous archive");
  f.env.PATH = originalPath;
  await f.exportLogs({});
  verify(f.destination);
});
