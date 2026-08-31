import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'tinyTouch',
  description: 'Documentation for tinyTouch hardware, software, and firmware.',
  lang: 'en-US',
  appearance: 'dark',
  cleanUrls: true,
  lastUpdated: true,
  metaChunk: true,
  sitemap: { hostname: 'https://docs.tinytouch.dev' },
  head: [
    ['meta', { name: 'theme-color', content: '#ffffff' }],
    ['meta', { name: 'color-scheme', content: 'light dark' }],
  ],
  markdown: {
    lineNumbers: false,
  },
  themeConfig: {
    siteTitle: '×  tinyTouch',
    logo: {
      light: 'https://alpacaengineer.ing/assets/alpaca.svg',
      dark: 'https://alpacaengineer.ing/assets/alpaca.svg',
      alt: 'Alpaca Engineer',
    },
    search: { provider: 'local' },
    nav: [
      { text: 'Guide', link: '/customer/' },
      { text: 'Flash', link: '/flash' },
      { text: 'Reference', link: '/reference/cli' },
    ],
    sidebar: [
      {
        text: 'Guide',
        items: [
          { text: 'Overview', link: '/customer/' },
          { text: 'Build', link: '/customer/build' },
          { text: 'Flash', link: '/flash' },
          { text: 'Setup', link: '/customer/setup' },
          { text: 'Recovery', link: '/customer/recovery' },
        ],
      },
      {
        text: 'Reference',
        collapsed: false,
        items: [
          { text: 'CLI commands', link: '/reference/cli' },
          { text: 'Recovery', link: '/reference/recovery' },
        ],
      },
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/ZimengXiong/TinyTouch' },
    ],
    editLink: {
      pattern: 'https://github.com/ZimengXiong/TinyTouch/edit/main/docs/:path',
      text: 'Edit this page on GitHub',
    },
    outline: { level: [2, 3], label: 'On this page' },
    docFooter: { prev: 'Previous', next: 'Next' },
    lastUpdated: { text: 'Last updated' },
  },
})
