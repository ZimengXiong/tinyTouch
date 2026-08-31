# TinyTouch API on Endeavour

The TinyTouch repository is the product authority for firmware, hardware,
desktop helper software, the documentation flasher, and this waitlist API. The API
is owned by `software/api`; it is not part of the separate Alpaca Engineer
Vercel application.

The Compose project remains `tinytouch-api`, so the existing
`tinytouch-api_tinytouch-data` named volume, `tinytouch-api` container, and
external `proxy` network are reused in place. The volume intentionally remains
under `/mnt/4TB/docker/volumes`.

Validate and deploy from this directory:

```sh
docker compose config --quiet
docker compose up -d --build
```

The hourly user-systemd timer creates one native SQLite backup, verifies it,
stores the local artifact and checksum under `/srv/backups/tinytouch`, and
publishes those same files to the existing private GitHub Release repository.
