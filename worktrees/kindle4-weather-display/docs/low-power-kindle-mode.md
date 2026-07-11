# Low-power Kindle dashboard mode

This repo now supports a low-power Kindle refresh mode that avoids keeping
Wi-Fi active between dashboard checks.

## What changed

- `refresh-once.sh` now performs one wake cycle:
  - loads device config
  - optionally enables Wi-Fi
  - performs a conditional image request against `/device/<device-id>/image.png`
  - updates the local PNG only when the image changed
  - refreshes e-ink only when the PNG changed
  - disables Wi-Fi again when `wifi_power_save` is enabled
- `refresh.sh` is now the long-running scheduler. It calls
  `refresh-once.sh`, then sleeps until the next configured wake cycle.
- The image server now returns `ETag` and `Last-Modified` headers and honors
  `If-None-Match` / `If-Modified-Since` with `304 Not Modified`.

## Device settings

Each Kindle device config supports:

- `refresh_interval_minutes`
  - allowed values: `5`, `10`, `15`, `30`, `60`
  - default for new configs: `60`
- `wifi_power_save`
  - `true`: Wi-Fi turns on only for refresh checks, then turns off again
  - `false`: Wi-Fi stays available between checks
- `update_only_if_changed`
  - `true`: uses cached validators and skips download/display refresh on `304`
  - `false`: always downloads the image

## Migration path

Existing devices are migrated by reinstalling or re-running the generated
installer script:

- `device.env` now includes:
  - `REFRESH_INTERVAL_MINUTES`
  - `WIFI_POWER_SAVE`
  - `UPDATE_ONLY_IF_CHANGED`
  - `CONFIG_URL`
- both `image.png` and legacy `weather.png` are kept compatible on-device
- `start.sh`, `stop.sh`, `watchdog.sh`, and `dashboard_loop.sh` remain in place
  so older automation paths still work

## Battery-saving design notes

- We keep the existing dashboard rendering pipeline unchanged.
- We avoid deep sleep / RTC wake assumptions in the default path because Kindle
  models differ and suspend reliability is inconsistent across devices.
- The main battery wins come from:
  - Wi-Fi disabled between refreshes
  - conditional HTTP validation
  - skipping unnecessary e-ink redraws
- The server still exposes the same image route:

```text
/device/<device-id>/image.png
```

That means existing device rendering, push flows, and previews keep working.
