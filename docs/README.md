# tinyTouch documentation

Welcome to the VitePress documentation site for tinyTouch.

## Run a local server

Start a local development server to preview your changes.

```sh
cd docs
npm install
npm run dev
```

## Build static files

Generate the site for deployment.

```sh
npm run build
```

You can find the generated site in `.vitepress/dist`.

Release automation writes one verified factory firmware image, CLI packages, and release metadata into `public/`. The Flash page uses that image for both normal installation and erase-and-reinstall recovery.
