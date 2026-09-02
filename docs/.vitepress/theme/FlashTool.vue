<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'

type ToolName = 'factory' | 'recovery' | 'beta'
type FlashPhase = 'select' | 'connected' | 'writing' | 'reset' | 'done'
type ManifestImage = { name: string; file: string; address: number; size: number; sha256: string }
type Manifest = {
  version: string
  protocol: number
  secureVersion: number
  flashSize: string
  eraseAll: boolean
  compress: boolean
  images: ManifestImage[]
}
type FirmwareFile = { data: Uint8Array; address: number }

const FLASH_BYTES = 4 * 1024 * 1024
const UPDATE_PROTOCOL = 6
const REQUIRED_ADDRESSES = [0x0, 0x8000, 0x10000, 0x210000]
const ESPTOOL_MODULE = '/flash/vendor/esptool-js.js'
const RELEASE_API = 'https://api.github.com/repos/ZimengXiong/tinyTouch/releases?per_page=20'

const selected = ref<ToolName>('factory')
const manifest = ref<Manifest | null>(null)
const firmwareFiles = ref<FirmwareFile[]>([])
const serialSupported = ref(false)
const busy = ref(false)
const progress = ref(0)
const stage = ref('Ready')
const status = ref('')
const statusKind = ref('')
const log = ref('No device activity yet.')
const logRef = ref<HTMLElement | null>(null)

function show(message: string, kind = '') {
  status.value = message
  statusKind.value = kind
}

function writeLog(value: string) {
  const line = value.trim()
  if (!line) return
  log.value = log.value === 'No device activity yet.' ? line : `${log.value}\n${line}`
  nextTick(() => {
    if (logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight
  })
}

function friendlyError(error: unknown, phase: FlashPhase = 'select', mode = selected.value) {
  const text = error instanceof Error ? error.message : String(error)
  const recovery = mode === 'recovery'
  if (/notfound|no port selected|chooser/i.test(text) && phase === 'select') {
    return recovery ? 'No board was selected. Nothing was erased.' : 'No board was selected. Nothing was flashed.'
  }
  if (phase === 'writing' || phase === 'reset') {
    return recovery
      ? `Recovery stopped after write operations began. ${text || 'Keep the board connected and retry recovery.'}`
      : `Flashing stopped after write operations began. ${text || 'Reconnect the board and use recovery before retrying.'}`
  }
  if (/securityerror|permission denied|access denied/i.test(text)) return 'Chrome does not have permission to use this serial port. Reload the page, select the board again, and approve access.'
  if (/already open|busy|networkerror/i.test(text)) return recovery
    ? 'The serial port is busy. Close tinyTouch helpers, serial monitors, and other flashing tabs, then try again.'
    : 'The serial port is busy or was disconnected. Close serial monitors and other flashing tabs, reconnect the board, then try again.'
  if (/connect|serial data|timeout|sync|bootloader/i.test(text)) return recovery
    ? 'The board is not in download mode. Hold BOOT, tap RESET, release BOOT, then try again.'
    : 'The ESP32-S3 did not enter download mode. Hold BOOT, tap RESET, release BOOT, then try again.'
  if (/could not be downloaded/i.test(text)) return `${text} Check your internet connection, reload the page, and try again.`
  if (/integrity check/i.test(text)) return `${text} Reload the page before trying again; do not flash a file that failed verification.`
  return text || (recovery ? 'Recovery stopped before completion.' : 'Flashing stopped. Nothing else was changed.')
}

async function sha256(data: ArrayBuffer) {
  const digest = await crypto.subtle.digest('SHA-256', data)
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

function releaseAsset(file: string, tag?: string) {
  const query = new URLSearchParams({ file })
  if (tag) query.set('tag', tag)
  return `/api/github-release?${query}`
}

async function loadManifest(mode: ToolName) {
  let tag: string | undefined
  if (mode === 'beta') {
    const releasesResponse = await fetch(RELEASE_API, { cache: 'no-store' })
    if (!releasesResponse.ok) throw new Error('Beta releases could not be downloaded.')
    const releases = await releasesResponse.json() as Array<{ draft: boolean; prerelease: boolean; tag_name: string }>
    const beta = releases.find((release) =>
      !release.draft && release.prerelease && /^v[0-9]+\.[0-9]+\.[0-9]+-beta(?:[.-][0-9A-Za-z.-]+)?$/.test(release.tag_name)
    )
    if (!beta) throw new Error('No beta release is available.')
    tag = beta.tag_name
  }
  const label = mode === 'factory' ? 'Firmware' : mode === 'recovery' ? 'Recovery' : 'Beta'
  const response = await fetch(releaseAsset('release-manifest.json', tag), { cache: 'no-store' })
  if (!response.ok) throw new Error(`${label} manifest could not be downloaded.`)
  const release = await response.json() as { firmware?: { factory?: Manifest } }
  const nextManifest = release.firmware?.factory
  if (!nextManifest || typeof nextManifest !== 'object' || typeof nextManifest.version !== 'string' ||
      nextManifest.protocol !== UPDATE_PROTOCOL || nextManifest.secureVersion !== 0 ||
      nextManifest.flashSize !== '4MB' || nextManifest.eraseAll !== false ||
      nextManifest.compress !== false || !Array.isArray(nextManifest.images) ||
      nextManifest.images.length !== REQUIRED_ADDRESSES.length) {
    throw new Error(`${label} manifest is incomplete.`)
  }
  const ranges: [number, number][] = []
  for (const image of nextManifest.images) {
    if (!image || typeof image.name !== 'string' || typeof image.file !== 'string' ||
        !/^[A-Za-z0-9._-]+$/.test(image.file) || !Number.isInteger(image.address) ||
        !REQUIRED_ADDRESSES.includes(image.address) || !Number.isInteger(image.size) ||
        image.size <= 0 || image.size > FLASH_BYTES || typeof image.sha256 !== 'string' ||
        !/^[0-9a-f]{64}$/.test(image.sha256) || image.address + image.size > FLASH_BYTES) {
      throw new Error(`${label} manifest contains an invalid flash image.`)
    }
    ranges.push([image.address, image.address + image.size])
  }
  if (new Set(nextManifest.images.map((image) => image.address)).size !== REQUIRED_ADDRESSES.length) {
    throw new Error(`${label} manifest contains duplicate flash regions.`)
  }
  ranges.sort((a, b) => a[0] - b[0])
  for (let index = 1; index < ranges.length; index += 1) {
    if (ranges[index][0] < ranges[index - 1][1]) throw new Error(`${label} flash regions overlap.`)
  }
  if (nextManifest.images.reduce((sum, image) => sum + image.size, 0) > FLASH_BYTES) {
    throw new Error(`${label} manifest is unexpectedly large.`)
  }
  return { tag, manifest: nextManifest }
}

async function loadFirmware(tag: string | undefined, currentManifest: Manifest) {
  const files: FirmwareFile[] = []
  for (const image of currentManifest.images) {
    const response = await fetch(releaseAsset(image.file, tag), { cache: 'no-store' })
    if (!response.ok) throw new Error(`${image.name} could not be downloaded.`)
    const buffer = await response.arrayBuffer()
    if (buffer.byteLength !== image.size) throw new Error(`${image.name} has the wrong file size.`)
    if (await sha256(buffer) !== image.sha256) throw new Error(`${image.name} failed its integrity check.`)
    files.push({ data: new Uint8Array(buffer), address: image.address })
  }
  return files
}

async function flash() {
  if (!serialSupported.value || busy.value) return
  let transport: { disconnect: () => Promise<void> } | undefined
  let phase: FlashPhase = 'select'
  const mode = selected.value
  const currentManifest = manifest.value
  const fileArray = firmwareFiles.value
  if (!currentManifest || fileArray.length !== currentManifest.images.length) return
  busy.value = true
  progress.value = 0
  stage.value = 'Connecting'
  log.value = 'No device activity yet.'
  show('Choose the ESP32-S3 serial port in the browser window.')
  try {
    const { ESPLoader, Transport } = await import(ESPTOOL_MODULE)
    stage.value = mode === 'recovery' ? 'Checking recovery firmware' : 'Checking firmware'
    const port = await navigator.serial.requestPort({ filters: [{ usbVendorId: 0x303a }] })
    const info = port.getInfo()
    const nativeDownloadMode = info.usbVendorId === 0x303a && [0x0009, 0x1001].includes(info.usbProductId ?? 0)
    transport = new Transport(port, false)
    const terminal = { clean: () => { log.value = '' }, write: writeLog, writeLine: writeLog }
    const loader = new ESPLoader({ transport, baudrate: 460800, terminal, debugLogging: false })
    phase = 'connected'
    show('Connecting to ESP32-S3…')
    const chip = await loader.main(nativeDownloadMode ? 'no_reset' : 'default_reset')
    if (!/ESP32-S3/i.test(chip)) throw new Error(`This is ${chip}, not an ESP32-S3.`)

    const totalBytes = currentManifest.images.reduce((sum, image) => sum + image.size, 0)
    const written = fileArray.map(() => 0)
    if (mode === 'recovery') {
      stage.value = 'Erasing flash'
      await loader.eraseFlash()
    }
    stage.value = mode === 'recovery' ? 'Writing factory firmware' : 'Writing firmware'
    phase = 'writing'
    await loader.writeFlash({
      fileArray,
      flashMode: 'dio',
      flashFreq: '80m',
      flashSize: currentManifest.flashSize,
      eraseAll: currentManifest.eraseAll,
      compress: currentManifest.compress,
      reportProgress(index: number, amount: number) {
        written[index] = amount
        progress.value = Math.min(100, Math.round(written.reduce((sum, item) => sum + item, 0) / totalBytes * 100))
      },
    })
    progress.value = 100
    phase = 'reset'
    try { await loader.after('hard_reset') } catch (error) { writeLog(`Reset notice: ${error}`) }
    try { await transport.disconnect() } catch {}
    transport = undefined
    phase = 'done'
    show(mode === 'recovery'
      ? 'Flash complete. The device was erased and the factory firmware was installed. Unplug and reconnect it once, then run tinytouch setup.'
      : 'Flash complete. Unplug the board and reconnect it once.', 'success')
  } catch (error) {
    show(friendlyError(error, phase, mode), 'error')
    try { await transport?.disconnect() } catch {}
  } finally {
    busy.value = false
    stage.value = mode === 'recovery' ? 'Recovery' : 'Flashing'
  }
}

async function selectTool() {
  const mode = selected.value
  manifest.value = null
  firmwareFiles.value = []
  show('Loading firmware…')
  try {
    const loaded = await loadManifest(mode)
    const files = await loadFirmware(loaded.tag, loaded.manifest)
    if (selected.value !== mode) return
    manifest.value = loaded.manifest
    firmwareFiles.value = files
    show('')
  } catch (error) {
    if (selected.value === mode) show(friendlyError(error, 'select', mode), 'error')
  }
}

onMounted(async () => {
  if (new URLSearchParams(window.location.search).get('firmware') === 'recovery') {
    selected.value = 'recovery'
  }
  serialSupported.value = 'serial' in navigator
  if (!serialSupported.value) show('Open this page in Google Chrome or Microsoft Edge.', 'error')
  await selectTool()
})
</script>

<template>
  <section class="flash-tool" :class="{ 'is-recovery': selected === 'recovery' }">
    <div class="flash-tool-controls">
      <label for="flash-version">Firmware</label>
      <select id="flash-version" v-model="selected" :disabled="busy" @change="selectTool">
        <option value="factory">Factory firmware</option>
        <option value="recovery">Recovery firmware</option>
        <option value="beta">Beta firmware</option>
      </select>
    </div>
    <div class="flash-tool-body">
      <p class="flash-description">
        {{ selected === 'recovery' ? 'Erase the device and reinstall tinyTouch.' : 'Install tinyTouch on a new ESP32-S3 board.' }}
      </p>
      <p class="flash-version">Version {{ manifest?.version ?? '…' }}</p>
      <div v-if="busy || progress > 0" class="flash-progress">
        <div><span>{{ stage }}</span><strong>{{ progress }}%</strong></div>
        <progress max="100" :value="progress">{{ progress }}%</progress>
      </div>
      <button type="button" :disabled="!serialSupported || busy || !manifest || firmwareFiles.length !== manifest.images.length" @click="flash">
        {{ selected === 'recovery' ? 'Erase and recover' : 'Connect and flash' }}
      </button>
      <div v-if="status" class="flash-status" :class="statusKind" role="status">{{ status }}</div>
      <pre ref="logRef" hidden>{{ log }}</pre>
    </div>
  </section>
</template>
