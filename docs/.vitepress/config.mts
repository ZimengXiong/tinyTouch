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
    siteTitle: '× tinyTouch',
    logo: {
      light: 'https://alpacaengineer.ing/assets/alpaca.svg',
      dark: 'https://alpacaengineer.ing/assets/alpaca.svg',
      alt: 'Alpaca Engineer',
    },
    search: { provider: 'local' },
    nav: [
      { text: 'Customers', link: '/customer/' },
      { text: 'Build your own', link: '/builder/' },
      { text: 'Reference', link: '/reference/cli' },
    ],
    sidebar: [
      {
        text: 'Start',
        items: [
          { text: 'Documentation home', link: '/' },
          { text: 'Choose a path', link: '/start' },
        ],
      },
      {
        text: 'For customers',
        collapsed: false,
        items: [
          { text: 'Customer guide', link: '/customer/' },
          { text: 'Set up your device', link: '/customer/setup' },
          { text: 'Choose PIV or HID', link: '/customer/modes' },
          { text: 'Use tinyTouch', link: '/customer/use' },
          { text: 'Manage your device', link: '/customer/manage' },
          { text: 'Update tinyTouch', link: '/customer/update' },
        ],
      },
      {
        text: 'Build your own',
        collapsed: false,
        items: [
          { text: 'Builder guide', link: '/builder/' },
          { text: 'Parts and tools', link: '/builder/hardware' },
          { text: 'Wiring', link: '/builder/wiring' },
          { text: 'Flash factory firmware', link: '/builder/flash' },
          { text: 'Assembly', link: '/builder/assembly' },
          { text: 'Bring-up checklist', link: '/builder/bring-up' },
        ],
      },
      {
        text: 'Reference',
        collapsed: false,
        items: [
          { text: 'CLI commands', link: '/reference/cli' },
          { text: 'Security model', link: '/reference/security' },
          { text: 'Firmware architecture', link: '/reference/firmware' },
          { text: 'macOS software', link: '/reference/software' },
          { text: 'Recovery', link: '/reference/recovery' },
          { text: 'Troubleshooting', link: '/reference/troubleshooting' },
          { text: 'FAQ and limitations', link: '/reference/faq' },
        ],
      },
      {
        text: 'Maintainers',
        collapsed: true,
        items: [
          { text: 'Firmware development', link: '/maintainers/firmware-development' },
          { text: 'Release process', link: '/maintainers/releases' },
          { text: 'API operations', link: '/maintainers/api' },
          { text: 'Repository map', link: '/maintainers/repository' },
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
