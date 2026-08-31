import DefaultTheme from 'vitepress/theme'
import FlashTool from './FlashTool.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('FlashTool', FlashTool)
  },
}
