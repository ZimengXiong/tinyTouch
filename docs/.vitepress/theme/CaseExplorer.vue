<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

type Board = 'supermini' | 'xiao'
type Revision = 'v1' | 'v2'
type CasePart = 'top' | 'bottom'

const cases = {
  supermini: {
    label: 'ESP32-S3 Super Mini',
    revisions: {
      v1: {
        label: 'V1',
        bundle: '/downloads/tinytouch-supermini-v1-case.zip',
        parts: {
          top: {
            label: 'Top',
            model: '/tinytouch-v1-top.glb',
            step: '/downloads/tinytouch-v1-top.step',
            dimensions: '30.0 × 30.0 × 15.2 mm',
          },
          bottom: {
            label: 'Base',
            model: '/tinytouch-supermini-v1-bottom.glb',
            step: '/downloads/tinytouch-supermini-v1-bottom.step',
            dimensions: '27.1 × 28.6 × 6.0 mm',
          },
        },
      },
      v2: {
        label: 'V2',
        bundle: '/downloads/tinytouch-supermini-v2-case.zip',
        parts: {
          top: {
            label: 'Top',
            model: '/tinytouch-v2-top.glb',
            step: '/downloads/tinytouch-v2-top.step',
            dimensions: '27.7 × 27.7 × 15.7 mm',
          },
          bottom: {
            label: 'Base',
            model: '/tinytouch-supermini-v2-bottom.glb',
            step: '/downloads/tinytouch-supermini-v2-bottom.step',
            dimensions: '25.5 × 25.5 × 12.9 mm',
          },
        },
      },
    },
  },
  xiao: {
    label: 'Seeed Studio XIAO ESP32-S3',
    revisions: {
      v1: {
        label: 'V1',
        bundle: '/downloads/tinytouch-xiao-v1-case.zip',
        parts: {
          top: {
            label: 'Top',
            model: '/tinytouch-v1-top.glb',
            step: '/downloads/tinytouch-v1-top.step',
            dimensions: '30.0 × 30.0 × 15.2 mm',
          },
          bottom: {
            label: 'Base',
            model: '/tinytouch-xiao-v1-bottom.glb',
            step: '/downloads/tinytouch-xiao-v1-bottom.step',
            dimensions: '27.0 × 28.5 × 6.0 mm',
          },
        },
      },
    },
  },
} as const

const board = ref<Board>('supermini')
const revision = ref<Revision>('v2')
const expanded = reactive<Record<CasePart, boolean>>({ top: false, bottom: false })

const boardConfig = computed(() => cases[board.value])
const availableRevisions = computed(() => Object.keys(boardConfig.value.revisions) as Revision[])
const revisionConfig = computed(() => boardConfig.value.revisions[revision.value as keyof typeof boardConfig.value.revisions])

function selectBoard(event: Event) {
  const value = (event.target as HTMLSelectElement).value as Board
  board.value = value
  if (!(revision.value in cases[value].revisions)) revision.value = 'v1'
}

function previewId(part: string) {
  return `case-preview-${board.value}-${revision.value}-${part}`
}

onMounted(() => import('@google/model-viewer'))
</script>

<template>
  <section class="case-explorer" aria-label="tinyTouch case downloads">
    <div class="case-configurator">
      <label>
        <span>Board</span>
        <select :value="board" aria-label="Board" @change="selectBoard">
          <option v-for="(item, key) in cases" :key="key" :value="key">{{ item.label }}</option>
        </select>
      </label>
      <label>
        <span>Version</span>
        <select v-model="revision" aria-label="Case version">
          <option v-for="key in availableRevisions" :key="key" :value="key">
            {{ boardConfig.revisions[key].label }}
          </option>
        </select>
      </label>
    </div>

    <div class="case-file-table-wrap">
      <table class="case-file-table">
        <thead>
          <tr>
            <th>Part</th>
            <th>Size</th>
            <th><span class="visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(item, key) in revisionConfig.parts" :key="key">
            <tr>
              <td><strong>{{ item.label }}</strong></td>
              <td>{{ item.dimensions }}</td>
              <td class="case-file-actions">
                <button
                  type="button"
                  :aria-controls="previewId(key)"
                  :aria-expanded="expanded[key]"
                  @click="expanded[key] = !expanded[key]"
                >
                  {{ expanded[key] ? 'Close 3D' : 'View 3D' }}
                </button>
                <a :href="item.step" download>STEP</a>
              </td>
            </tr>
            <tr v-if="expanded[key]" class="case-preview-row">
              <td colspan="3">
                <div :id="previewId(key)" class="case-preview">
                  <model-viewer
                    :src="item.model"
                    :alt="`Interactive 3D model of the ${boardConfig.label} ${revisionConfig.label} case ${item.label.toLowerCase()}`"
                    camera-controls
                    camera-orbit="45deg 38deg 300%"
                    field-of-view="40deg"
                    max-field-of-view="45deg"
                    exposure="0.8"
                    tone-mapping="aces"
                    shadow-intensity="1"
                    shadow-softness="1"
                    environment-image="/case-studio.hdr"
                    interaction-prompt="auto"
                    touch-action="pan-y"
                  >
                    <div slot="poster" class="case-explorer-loading">Loading 3D…</div>
                  </model-viewer>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
        <tfoot>
          <tr>
            <td colspan="2"></td>
            <td class="case-file-actions"><a :href="revisionConfig.bundle" download>Download all</a></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </section>
</template>
