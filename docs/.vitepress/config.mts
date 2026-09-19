import { defineConfig } from 'vitepress'
import githubReleaseHandler from '../api/github-release.js'

const siteOrigin = process.env.TINYTOUCH_SITE_ORIGIN ?? 'https://docs.tinytouch.dev'

function githubReleaseDevApi() {
  return {
    name: 'tinytouch-github-release-api',
    apply: 'serve' as const,
    configureServer(server) {
      server.middlewares.use('/api/github-release', async (request, response, next) => {
        const url = new URL(request.url ?? '', 'http://localhost')
        const query = Object.fromEntries(url.searchParams)
        const adapter = {
          setHeader(name, value) {
            response.setHeader(name, value)
          },
          status(code) {
            response.statusCode = code
            return adapter
          },
          end(payload) {
            response.end(payload)
          },
        }

        try {
          await githubReleaseHandler({ method: request.method, query }, adapter)
        } catch (error) {
          next(error)
        }
      })
    },
  }
}

export default defineConfig({
  title: 'tinyTouch',
  description: 'Documentation for tinyTouch hardware, software, and firmware.',
  lang: 'en-US',
  appearance: 'dark',
  cleanUrls: true,
  lastUpdated: true,
  metaChunk: true,
  sitemap: { hostname: siteOrigin },
  head: [
    ['link', { rel: 'icon', type: 'image/png', href: '/tinytouch.png' }],
    ['meta', { name: 'theme-color', content: '#ffffff' }],
    ['meta', { name: 'color-scheme', content: 'light dark' }],
  ],
  markdown: {
    lineNumbers: false,
  },
  vue: {
    template: {
      compilerOptions: {
        isCustomElement: (tag) => tag === 'model-viewer',
      },
    },
  },
  vite: {
    plugins: [githubReleaseDevApi()],
    server: {
      allowedHosts: ['.v3c.dev'],
    },
  },
  themeConfig: {
    siteTitle: '×  tinyTouch',
    search: {
      provider: 'local',
    },
    logo: {
      light: 'https://alpacaengineer.ing/assets/alpaca.svg',
      dark: 'https://alpacaengineer.ing/assets/alpaca.svg',
      alt: 'Alpaca Engineer',
    },
    nav: [
      { text: 'Guide', link: '/customer/build' },
      { text: 'Flash', link: '/flash' },
      { text: 'Reference', link: '/reference/cli' },
    ],
    sidebar: [
      {
        text: 'Guide',
        items: [
          { text: '1. Build', link: '/customer/build' },
          { text: '2. Install firmware', link: '/flash' },
          { text: '3. Setup', link: '/customer/setup' },
        ],
      },
      {
        text: 'Misc',
        collapsed: true,
        items: [
          { text: 'Update', link: '/customer/update' },
          { text: 'Recovery', link: '/customer/recovery' },
          { text: 'Troubleshooting', link: '/customer/troubleshooting' },
        ],
      },
      {
        text: 'Reference',
        collapsed: true,
        items: [
          { text: 'CLI commands', link: '/reference/cli' },
          { text: 'Device configuration', link: '/reference/configuration' },
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
