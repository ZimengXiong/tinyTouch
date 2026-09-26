import DefaultTheme from 'vitepress/theme'
import CaseExplorer from './CaseExplorer.vue'
import FlashTool from './FlashTool.vue'
import SensorPinout from './SensorPinout.vue'
import StoreIcon from './StoreIcon.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('CaseExplorer', CaseExplorer)
    app.component('FlashTool', FlashTool)
    app.component('SensorPinout', SensorPinout)
    app.component('StoreIcon', StoreIcon)
  },
}
