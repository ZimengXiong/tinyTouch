const RELEASE_TAG = /^v[0-9]+\.[0-9]+\.[0-9]+-beta(?:[.-][0-9A-Za-z.-]+)?$/
const RELEASE_ASSETS = new Set([
  'release-manifest.json',
  'bootloader.bin',
  'partition-table.bin',
  'ota_data_initial.bin',
  'tiny_touch_unified.bin',
])

export default async function handler(request, response) {
  const file = request.query.file
  const tag = request.query.tag
  if (
    request.method !== 'GET' ||
    typeof file !== 'string' ||
    !RELEASE_ASSETS.has(file) ||
    (tag !== undefined && (typeof tag !== 'string' || !RELEASE_TAG.test(tag)))
  ) {
    response.status(404).end()
    return
  }

  const release = tag ? `download/${tag}` : 'latest/download'
  const upstream = await fetch(
    `https://github.com/ZimengXiong/tinyTouch/releases/${release}/${file}`,
    { redirect: 'follow' },
  )
  if (!upstream.ok) {
    response.status(upstream.status).end()
    return
  }

  const payload = Buffer.from(await upstream.arrayBuffer())
  response.setHeader('Content-Type', upstream.headers.get('content-type') || 'application/octet-stream')
  response.setHeader('Content-Length', String(payload.length))
  response.setHeader(
    'Cache-Control',
    tag ? 'public, max-age=31536000, immutable' : 'public, max-age=60',
  )
  response.status(200).end(payload)
}
