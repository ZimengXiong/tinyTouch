import { ESPLoader, Transport } from "./vendor/esptool-js.js";

const button = document.querySelector("#flash");
const message = document.querySelector("#message");
const browserNote = document.querySelector("#browser-note");
const progressWrap = document.querySelector("#progress-wrap");
const progress = document.querySelector("#progress");
const percent = document.querySelector("#percent");
const stage = document.querySelector("#stage");
const log = document.querySelector("#log");
const firmwareVersion = document.querySelector("#firmware-version");
const serialSupported = "serial" in navigator;

if (!serialSupported) {
  browserNote.textContent = "Open this page in Google Chrome or Microsoft Edge.";
}

function writeLog(value) {
  const line = value.trim();
  if (!line) return;
  log.textContent = log.textContent === "No device activity yet." ? line : `${log.textContent}\n${line}`;
  log.scrollTop = log.scrollHeight;
}

function show(text, kind = "") {
  message.textContent = text;
  message.className = `message ${kind}`.trim();
}

async function sha256(data) {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function loadManifest() {
  const manifestResponse = await fetch("./manifest.json", { cache: "no-store" });
  if (!manifestResponse.ok) throw new Error("Recovery manifest could not be downloaded.");
  const manifest = await manifestResponse.json();
  if (!manifest.version || !Array.isArray(manifest.images)) {
    throw new Error("Recovery manifest is incomplete.");
  }
  return manifest;
}

const manifestPromise = loadManifest();
manifestPromise.then((manifest) => {
  firmwareVersion.textContent = manifest.version;
  button.disabled = !serialSupported;
}).catch((error) => {
  firmwareVersion.textContent = "unavailable";
  show(friendlyError(error), "error");
});

async function loadFirmware() {
  const manifest = await manifestPromise;
  const loaded = [];
  for (const image of manifest.images) {
    const response = await fetch(`./firmware/${image.file}`, { cache: "no-store" });
    const { name, address, sha256: expected, size } = image;
    if (!response.ok) throw new Error(`${name} could not be downloaded.`);
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength !== size) throw new Error(`${name} has the wrong file size.`);
    if (await sha256(buffer) !== expected) throw new Error(`${name} failed its integrity check.`);
    loaded.push({ data: new Uint8Array(buffer), address });
  }
  return { files: loaded, manifest };
}

function friendlyError(error) {
  const text = error instanceof Error ? error.message : String(error);
  if (/notfound|no port selected|chooser/i.test(text)) return "No board was selected. Nothing was erased.";
  if (/already open|busy|networkerror/i.test(text)) return "The serial port is busy. Close tinyTouch helpers, serial monitors, and other flashing tabs, then try again.";
  if (/connect|serial data|timeout|sync/i.test(text)) return "The board is not in download mode. Hold BOOT, tap RESET, release BOOT, then try again.";
  return text || "Recovery stopped before completion.";
}

button.addEventListener("click", async () => {
  let transport;
  button.disabled = true;
  progressWrap.hidden = false;
  progress.value = 0;
  percent.textContent = "0%";
  log.textContent = "No device activity yet.";
  show("Choose the ESP32-S3 download-mode serial port.");
  try {
    const port = await navigator.serial.requestPort();
    const { usbVendorId, usbProductId } = port.getInfo();
    const nativeDownloadMode = usbVendorId === 0x303a && [0x0009, 0x1001].includes(usbProductId);
    transport = new Transport(port, false);
    const terminal = { clean(){ log.textContent = ""; }, write:writeLog, writeLine:writeLog };
    const loader = new ESPLoader({ transport, baudrate:460800, terminal, debugLogging:false });
    show("Connecting to ESP32-S3…");
    const chip = await loader.main(nativeDownloadMode ? "no_reset" : "default_reset");
    if (!/ESP32-S3/i.test(chip)) throw new Error(`This is ${chip}, not an ESP32-S3.`);

    stage.textContent = "Checking recovery firmware";
    const { files: fileArray, manifest } = await loadFirmware();
    const totalBytes = fileArray.reduce((sum, file) => sum + file.data.length, 0);
    const written = fileArray.map(() => 0);
    stage.textContent = "Writing recovery firmware";
    await loader.writeFlash({
      fileArray, flashMode:"dio", flashFreq:"80m", flashSize:manifest.flashSize,
      eraseAll:manifest.eraseAll, compress:manifest.compress,
      reportProgress(index, amount) {
        written[index] = amount;
        const value = Math.min(100, Math.round(written.reduce((sum, item) => sum + item, 0) / totalBytes * 100));
        progress.value = value;
        percent.textContent = `${value}%`;
      },
    });
    progress.value = 100;
    percent.textContent = "100%";
    stage.textContent = "Starting one-time erase";
    await loader.after("hard_reset");
    await transport.disconnect();
    transport = undefined;
    show("Recovery firmware installed. Unplug and reconnect the device, wait 20 seconds, then run tinytouch setup.", "success");
    button.textContent = "Recover another device";
  } catch (error) {
    show(friendlyError(error), "error");
    try { await transport?.disconnect(); } catch {}
  } finally {
    button.disabled = !serialSupported;
  }
});
