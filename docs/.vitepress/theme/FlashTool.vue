<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'

type ToolName = 'factory' | 'recovery'
type ManifestImage = { name: string; file: string; address: number; size: number; sha256: string }
type Manifest = {
  version: string
  flashSize: string
  eraseAll: boolean
  compress: boolean
  images: ManifestImage[]
}

const selected = ref<ToolName>('factory')
const manifest = ref<Manifest | null>(null)
const serialSupported = ref(false)
const busy = ref(false)
const progress = ref(0)
const stage = ref('Ready')
const status = ref('Choose a board to start.')
const statusKind = ref('')
const log = ref('No device activity yet.')
const logRef = ref<HTMLElement | null>(null)

const tool = computed(() => ({
  factory: { label: 'Factory firmware', base: '/flash/factory' },
  recovery: { label: 'Recovery firmware', base: '/flash/recovery' },
}[selected.value]))

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

function friendlyError(error: unknown) {
  const text = error instanceof Error ? error.message : String(error)
  if (/notfound|no port selected|chooser/i.test(text)) return 'No board was selected. Nothing was flashed.'
  if (/securityerror|permission denied|access denied/i.test(text)) return 'Chrome does not have permission to use this serial port. Reload the page and select the board again.'
  if (/already open|busy|networkerror/i.test(text)) return 'The serial port is busy or disconnected. Close serial monitors and other flashing tabs, then reconnect the board.'
  if (/connect|serial data|timeout|sync|bootloader/i.test(text)) return 'The ESP32-S3 did not enter download mode. Hold BOOT, tap RESET, release BOOT, and try again.'
  return text || 'Flashing stopped. Nothing else was changed.'
}

async function sha256(data: ArrayBuffer) {
  const digest = await crypto.subtle.digest('SHA-256', data)
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function loadManifest() {
  const response = await fetch(`${tool.value.base}/manifest.json`, { cache: 'no-store' })
  if (!response.ok) throw new Error('Firmware manifest could not be downloaded.')
  const nextManifest = await response.json() as Manifest
  if (!nextManifest.version || !Array.isArray(nextManifest.images)) throw new Error('Firmware manifest is incomplete.')
  manifest.value = nextManifest
}

async function loadFirmware(currentManifest: Manifest) {
  const files = []
  for (const image of currentManifest.images) {
    const response = await fetch(`${tool.value.base}/firmware/${image.file}`, { cache: 'no-store' })
    if (!response.ok) throw new Error(`${image.name} could not be downloaded.`)
    const buffer = await response.arrayBuffer()
    if (buffer.byteLength !== image.size || await sha256(buffer) !== image.sha256) {
      throw new Error(`${image.name} failed its integrity check.`)
    }
    files.push({ data: new Uint8Array(buffer), address: image.address })
  }
  return files
}

async function flash() {
  if (!serialSupported.value || busy.value) return
  let transport: { disconnect: () => Promise<void> } | undefined
  busy.value = true
  progress.value = 0
  stage.value = 'Connecting'
  log.value = 'No device activity yet.'
  show('Choose the ESP32-S3 serial port in the browser window.')
  try {
    const { ESPLoader, Transport } = await import(`${tool.value.base}/vendor/esptool-js.js`)
    const port = await navigator.serial.requestPort()
    const info = port.getInfo()
    const nativeDownloadMode = info.usbVendorId === 0x303a && [0x0009, 0x1001].includes(info.usbProductId ?? 0)
    transport = new Transport(port, false)
    const terminal = { clean: () => { log.value = '' }, write: writeLog, writeLine: writeLog }
    const loader = new ESPLoader({ transport, baudrate: 460800, terminal, debugLogging: false })
    const chip = await loader.main(nativeDownloadMode ? 'no_reset' : 'default_reset')
    if (!/ESP32-S3/i.test(chip)) throw new Error(`This is ${chip}, not an ESP32-S3.`)

    stage.value = 'Checking firmware'
    const currentManifest = manifest.value ?? await loadManifest().then(() => manifest.value as Manifest)
    const fileArray = await loadFirmware(currentManifest)
    const totalBytes = currentManifest.images.reduce((sum, image) => sum + image.size, 0)
    const written = fileArray.map(() => 0)
    stage.value = currentManifest.eraseAll ? 'Erasing and recovering' : 'Writing firmware'
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
    await loader.after('hard_reset')
    await transport.disconnect()
    transport = undefined
    show(currentManifest.eraseAll ? 'Recovery complete. Reconnect the board, wait 20 seconds, then run tinytouch setup.' : 'Flash complete. Unplug the board and reconnect it once.', 'success')
  } catch (error) {
    show(friendlyError(error), 'error')
    try { await transport?.disconnect() } catch {}
  } finally {
    busy.value = false
    stage.value = manifest.value?.eraseAll ? 'Recovery' : 'Flashing'
  }
}

async function selectTool() {
  manifest.value = null
  show('Loading firmware manifest…')
  try {
    await loadManifest()
    show('Choose a board to start.')
  } catch (error) {
    show(friendlyError(error), 'error')
  }
}

onMounted(async () => {
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
      </select>
    </div>
    <div class="flash-tool-body">
      <p class="flash-version">Firmware {{ manifest?.version ?? 'loading…' }}</p>
      <ol>
        <li v-if="selected === 'recovery'">Hold BOOT, tap RESET, and release BOOT.</li>
        <li v-else>Connect the board by USB.</li>
        <li>Click the button and select the board.</li>
      </ol>
      <div class="flash-status" :class="statusKind" role="status">{{ status }}</div>
      <div v-if="busy || progress > 0" class="flash-progress">
        <div><span>{{ stage }}</span><strong>{{ progress }}%</strong></div>
        <progress max="100" :value="progress">{{ progress }}%</progress>
      </div>
      <button type="button" :disabled="!serialSupported || busy || !manifest" @click="flash">
        {{ selected === 'recovery' ? 'Erase and recover' : 'Connect and flash' }}
      </button>
      <pre ref="logRef" hidden>{{ log }}</pre>
    </div>
  </section>
</template>
