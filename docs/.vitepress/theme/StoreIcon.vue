<script setup lang="ts">
const props = defineProps<{
  href: string
  store: 'Amazon' | 'AliExpress' | 'YouTube'
}>()

const icons = {
  Amazon: '/store-icons/amazon.ico',
  AliExpress: '/store-icons/aliexpress.png',
  YouTube: '/store-icons/youtube.png',
}

const labels = {
  Amazon: 'Buy from Amazon',
  AliExpress: 'Buy from AliExpress',
  YouTube: 'Watch the video guide on YouTube',
}
</script>

<template>
  <a
    :class="['store-icon', { 'store-icon--youtube': props.store === 'YouTube' }]"
    :href="props.href"
    :aria-label="labels[props.store]"
    :title="props.store"
    target="_blank"
    rel="noopener noreferrer"
  >
    <img :src="icons[props.store]" alt="" />
  </a>
</template>
