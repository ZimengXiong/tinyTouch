// Loaded only for an authenticated touch, then unloaded by the helper.
// Pass identifiers, never titles, to the private per-request D-Bus connection.
(function () {
    const window = workspace.activeWindow;
    const app = window ? String(window.desktopFileName || "") : "";
    callDBus(DESTINATION, "/org/tinytouch/Query", "org.tinytouch.Query",
             "Report", TOKEN, app);
})();
