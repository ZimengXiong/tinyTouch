<template>
  <div class="sensor-pinout">
    <div class="sensor-pinout__visuals">
      <svg class="sensor-pinout__wiring" viewBox="0 0 1200 520" role="img" aria-labelledby="wiring-title wiring-desc">
        <title id="wiring-title">tinyTouch sensor wiring</title>
        <desc id="wiring-desc">The fingerprint sensor wired to the ESP32-S3 Super Mini.</desc>

        <g transform="translate(40 70) scale(.75)">
        <title id="sensor-pinout-title">Fingerprint sensor connector pin numbering</title>
        <desc id="sensor-pinout-desc">Front view of the sensor with connector pins numbered one through six from left to right.</desc>

        <circle class="sensor-pinout__body" cx="210" cy="200" r="172" />
        <circle class="sensor-pinout__ring" cx="210" cy="200" r="157" />

        <g class="sensor-pinout__connector">
          <rect x="108" y="204" width="204" height="88" rx="6" />
          <rect x="130" y="242" width="160" height="42" />
        </g>

        <g class="sensor-pinout__numbers" text-anchor="middle">
          <text x="150" y="234">1</text>
          <text x="174" y="234">2</text>
          <text x="198" y="234">3</text>
          <text x="222" y="234">4</text>
          <text x="246" y="234">5</text>
          <text x="270" y="234">6</text>
        </g>

        <g class="sensor-pinout__contacts">
          <line class="pin-3v3" x1="150" y1="252" x2="150" y2="273" />
          <line class="pin-gpio2" x1="174" y1="252" x2="174" y2="273" />
          <line class="pin-3v3" x1="198" y1="252" x2="198" y2="273" />
          <line class="pin-rx" x1="222" y1="252" x2="222" y2="273" />
          <line class="pin-tx" x1="246" y1="252" x2="246" y2="273" />
          <line class="pin-gnd" x1="270" y1="252" x2="270" y2="273" />
        </g>
        </g>

        <image href="/supermini-used-pins.svg" x="560" y="50" width="600" height="336" />

        <g class="sensor-pinout__routes">
          <path class="route-visible route-tx" d="M224.5 274.75 V310 H430 V76 H571" />
          <path class="route-visible route-rx" d="M206.5 274.75 V330 H460 V109 H571" />
          <path class="route-visible route-gpio2" d="M170.5 274.75 V350 H490 V176 H571" />
          <path class="route-visible route-3v3 route-vtouch" d="M152.5 274.75 V450 H188.5" />
          <path class="route-visible route-3v3 route-vcc" d="M188.5 274.75 V450" />
          <path class="route-visible route-3v3 route-3v3-shared" d="M188.5 450 H1170 V142 H1147" />
          <path class="route-visible route-gnd" d="M242.5 274.75 V420 H1160 V109 H1147" />
          <path class="route-hit route-hit-tx" d="M224.5 274.75 V310 H430 V76 H571" />
          <path class="route-hit route-hit-rx" d="M206.5 274.75 V330 H460 V109 H571" />
          <path class="route-hit route-hit-gpio2" d="M170.5 274.75 V350 H490 V176 H571" />
          <path class="route-hit route-hit-vtouch" d="M152.5 274.75 V450 H188.5" />
          <path class="route-hit route-hit-vcc" d="M188.5 274.75 V450" />
          <path class="route-hit route-hit-3v3-shared" d="M188.5 450 H1170 V142 H1147" />
          <path class="route-hit route-hit-gnd" d="M242.5 274.75 V420 H1160 V109 H1147" />
        </g>
      </svg>
    </div>

    <div class="sensor-pinout__table">
      <table>
        <colgroup>
          <col class="sensor-pinout__pin-column" />
          <col />
          <col />
          <col />
        </colgroup>
        <thead>
          <tr>
            <th>Pin</th>
            <th>Signal</th>
            <th>Super Mini</th>
            <th>XIAO</th>
          </tr>
        </thead>
        <tbody>
          <tr class="row-3v3 row-vtouch"><td>1</td><td>VTouch</td><td>3V3</td><td>3V3</td></tr>
          <tr class="row-gpio2"><td>2</td><td>TouchOut</td><td>GPIO2</td><td>D1</td></tr>
          <tr class="row-3v3 row-vcc"><td>3</td><td>VCC</td><td>3V3</td><td>3V3</td></tr>
          <tr class="row-rx"><td>4</td><td>TX</td><td>RX</td><td>D7</td></tr>
          <tr class="row-tx"><td>5</td><td>RX</td><td>TX</td><td>D6</td></tr>
          <tr class="row-gnd"><td>6</td><td>GND</td><td>GND</td><td>GND</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.sensor-pinout {
  border: 1px solid var(--vp-c-divider);
  border-radius: 10px;
  overflow: hidden;
  padding: 24px;
}

.sensor-pinout__visuals {
  width: 100%;
}

.sensor-pinout__wiring {
  display: block;
  height: auto;
  width: 100%;
}

.sensor-pinout__body {
  fill: var(--vp-c-bg-alt);
  stroke: var(--vp-c-text-1);
  stroke-width: 4;
}

.sensor-pinout__ring {
  fill: none;
  stroke: var(--vp-c-text-2);
  stroke-width: 2;
}

.sensor-pinout__connector rect:first-child {
  fill: var(--vp-c-bg);
  stroke: var(--vp-c-text-1);
  stroke-width: 3;
}

.sensor-pinout__connector rect:last-child {
  fill: var(--vp-c-bg-alt);
  stroke: var(--vp-c-text-2);
  stroke-width: 2;
}

.sensor-pinout__numbers {
  fill: var(--vp-c-text-1);
  font-family: var(--vp-font-family-mono);
  font-size: 18px;
  font-weight: 700;
}

.sensor-pinout__contacts {
  stroke-linecap: round;
  stroke-width: 6;
}

.pin-tx { stroke: #00d9ff; }
.pin-rx { stroke: #c026ff; }
.pin-gpio2 { stroke: #ffd000; }
.pin-3v3 { stroke: #ff3b4e; }
.pin-gnd { stroke: var(--vp-c-text-1); }

.sensor-pinout__routes {
  pointer-events: none;
}

.sensor-pinout__routes path {
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2;
  transition: opacity 140ms ease, stroke-width 140ms ease;
  vector-effect: non-scaling-stroke;
}

.sensor-pinout__routes .route-hit {
  pointer-events: stroke;
  stroke: transparent;
  stroke-width: 16;
}

.route-tx { stroke: #00d9ff; }
.route-rx { stroke: #c026ff; }
.route-gpio2 { stroke: #ffd000; }
.route-3v3 { stroke: #ff3b4e; }
.route-gnd { stroke: var(--vp-c-text-1); }

.sensor-pinout__table {
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  margin-top: 20px;
  min-width: 0;
  overflow: hidden;
}

.sensor-pinout__table table {
  display: table !important;
  font-size: 12px;
  margin: 0;
  table-layout: fixed;
  width: 100%;
}

.sensor-pinout__pin-column {
  width: 12%;
}

.sensor-pinout__table th,
.sensor-pinout__table td {
  padding: 9px 8px;
}

.sensor-pinout__table tbody tr td:first-child {
  border-left: 3px solid transparent;
}

.sensor-pinout__table tbody tr {
  transition: background-color 140ms ease, opacity 140ms ease;
}

.sensor-pinout__table tbody tr:hover {
  background: var(--vp-c-bg-soft);
  opacity: 1 !important;
}

.sensor-pinout:has(.sensor-pinout__table tbody tr:hover) .sensor-pinout__routes path {
  opacity: 0.14;
}

.sensor-pinout:has(.sensor-pinout__table tbody tr:hover) .sensor-pinout__table tbody tr {
  opacity: 0.14;
}

.sensor-pinout:has(.row-tx:hover) .route-tx,
.sensor-pinout:has(.row-rx:hover) .route-rx,
.sensor-pinout:has(.row-gpio2:hover) .route-gpio2,
.sensor-pinout:has(.row-vtouch:hover) .route-vtouch,
.sensor-pinout:has(.row-vtouch:hover) .route-3v3-shared,
.sensor-pinout:has(.row-vcc:hover) .route-vcc,
.sensor-pinout:has(.row-vcc:hover) .route-3v3-shared,
.sensor-pinout:has(.row-gnd:hover) .route-gnd {
  opacity: 1 !important;
  stroke-width: 3;
}

.sensor-pinout:has(.route-hit:hover) .route-visible,
.sensor-pinout:has(.route-hit:hover) .sensor-pinout__table tbody tr {
  opacity: 0.14;
}

.sensor-pinout:has(.route-hit-tx:hover) .route-tx,
.sensor-pinout:has(.route-hit-rx:hover) .route-rx,
.sensor-pinout:has(.route-hit-gpio2:hover) .route-gpio2,
.sensor-pinout:has(.route-hit-vtouch:hover) .route-vtouch,
.sensor-pinout:has(.route-hit-vtouch:hover) .route-3v3-shared,
.sensor-pinout:has(.route-hit-vcc:hover) .route-vcc,
.sensor-pinout:has(.route-hit-vcc:hover) .route-3v3-shared,
.sensor-pinout:has(.route-hit-3v3-shared:hover) .route-3v3,
.sensor-pinout:has(.route-hit-gnd:hover) .route-gnd {
  opacity: 1 !important;
  stroke-width: 3;
}

.sensor-pinout:has(.route-hit-tx:hover) .row-tx,
.sensor-pinout:has(.route-hit-rx:hover) .row-rx,
.sensor-pinout:has(.route-hit-gpio2:hover) .row-gpio2,
.sensor-pinout:has(.route-hit-vtouch:hover) .row-vtouch,
.sensor-pinout:has(.route-hit-vcc:hover) .row-vcc,
.sensor-pinout:has(.route-hit-3v3-shared:hover) .row-3v3,
.sensor-pinout:has(.route-hit-gnd:hover) .row-gnd {
  background: var(--vp-c-bg-soft);
  opacity: 1 !important;
}

.sensor-pinout__table .row-tx td:first-child { border-left-color: #00d9ff; }
.sensor-pinout__table .row-rx td:first-child { border-left-color: #c026ff; }
.sensor-pinout__table .row-gpio2 td:first-child { border-left-color: #ffd000; }
.sensor-pinout__table .row-3v3 td:first-child { border-left-color: #ff3b4e; }
.sensor-pinout__table .row-gnd td:first-child { border-left-color: var(--vp-c-text-1); }

.sensor-pinout__table .row-tx td:nth-child(2),
.sensor-pinout__table .row-tx td:nth-child(3) { color: #00d9ff; }
.sensor-pinout__table .row-rx td:nth-child(2),
.sensor-pinout__table .row-rx td:nth-child(3) { color: #c026ff; }
.sensor-pinout__table .row-gpio2 td:nth-child(2),
.sensor-pinout__table .row-gpio2 td:nth-child(3) { color: #ffd000; }
.sensor-pinout__table .row-3v3 td:nth-child(2),
.sensor-pinout__table .row-3v3 td:nth-child(3) { color: #ff3b4e; }

@media (max-width: 760px) {
  .sensor-pinout {
    padding: 16px;
  }

  .sensor-pinout__visuals {
    margin: 0;
  }

  .sensor-pinout__table {
    margin-top: 12px;
  }
}

@media (max-width: 520px) {
  .sensor-pinout__table table {
    font-size: 11px;
  }

  .sensor-pinout__table th,
  .sensor-pinout__table td {
    padding: 7px 5px;
  }
}
</style>
