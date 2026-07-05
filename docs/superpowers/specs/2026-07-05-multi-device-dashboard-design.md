# Multi-Device Dashboard Architecture

## Goal

Evolve the existing single-Kindle dashboard into a server that manages multiple
Kindles and prepares for future ESP32 e-paper displays, while keeping the current
Kindle, `/weather.png`, `dashboard_config.json`, timer, settings UI, public
endpoint, and command-line generator working throughout the migration.

## Chosen approach

Use a registry-first architecture with a backward-compatibility facade.

- `devices.json` is the authoritative device registry.
- Each device has its own validated config and generated image.
- The current single-device paths remain aliases for `default-kindle`.
- Existing runtime services remain in place; their internals call the new
  registry and rendering APIs.
- Migration is idempotent: missing per-device files are initialized from the
  existing legacy config/image, but existing per-device files are never
  overwritten.

This is preferred over:

1. Keeping global state and adding device query parameters, which would leave
   rendering and saving coupled to one config and output file.
2. Replacing all legacy paths immediately, which would risk breaking the live
   Kindle and public Funnel endpoint during deployment.

## Files and responsibilities

### New modules

- `device_registry.py`
  - Validates device IDs and registry records.
  - Loads and atomically writes `devices.json`.
  - Resolves safe project-relative config and image paths.
  - Creates the default registry and migrates legacy files idempotently.
  - Returns public device metadata without server-side SSH secrets.

- `device_renderer.py`
  - Loads a device and its config.
  - Invokes the existing theme renderer with explicit output, resolution, notes,
    lock, and render-state paths.
  - Implements freshness checks and per-device generation locks.
  - Exposes `render_device(device_id, force=False)`.

- `test_device_registry.py`
  - Covers schema validation, safe paths, migration, and public serialization.

- `test_device_renderer.py`
  - Covers default and named-device rendering, output isolation, resolution,
    caching, and notes filtering.

### New runtime data

- `devices.json`
- `devices/default-kindle/config.json`
- `devices/default-kindle/image.png`

Runtime registry/config/image files remain local and are excluded from Git.
Tests create isolated temporary registries and device folders.

### Existing files to modify

- `weather_image.py`
  - Remove rendering dependence on global `OUT`, `CONFIG_PATH`, lock, notes, and
    render-state paths by passing an explicit render context.
  - Keep existing renderer functions and visual output unchanged for 758×1024.
  - Add CLI parsing for `--device`.
  - Keep `python3 weather_image.py` equivalent to
    `python3 weather_image.py --device default-kindle`.
  - Preserve legacy helper calls used by current tests and services.

- `serve_image.py`
  - Serve `/device/<device_id>/image.png`.
  - Validate IDs through the registry.
  - Return a fresh cached PNG or regenerate when stale.
  - Keep `/weather.png` as the default device alias.
  - Add `/device/<device_id>/image.bmp` as an explicit `501 Not Implemented`
    placeholder only for registered `esp32_epaper` devices.
  - Preserve battery query handling for `/weather.png`.

- `settings_server.py`
  - Add device registry routes and a Devices tab.
  - Track the selected device explicitly in the form/API.
  - Load/save/regenerate the selected device config.
  - Preserve `/api/config` and legacy form behavior as aliases for
    `default-kindle`.
  - Keep all existing CSRF, Device Controls, Maintenance, Daily Notes, city
    search, theme selection, display flags, prayer settings, frontlight, and
    refresh interval behavior.

- `kindle_device.py`
  - Replace the single hard-coded host with a device-aware factory.
  - Resolve `ssh_profile` through server-side `SSH_PROFILES`.
  - Build SSH arguments without `shell=True`.
  - Keep fixed action commands and validation.
  - Never return key or known-hosts paths through APIs.
  - For `default-kindle` only, retain the existing connection as a compatibility
    fallback when the connection block is absent.

- `public_image_server.py`
  - Continue exposing only authenticated `/weather.png`.
  - Resolve it through `default-kindle`; do not expose arbitrary device IDs
    through Funnel in this phase.

- `kindle_scripts/refresh.sh`
- `kindle_scripts/refresh-once.sh`
  - Read `/mnt/us/dashboard/device-id`; use `default-kindle` when missing/empty.
  - Validate the local ID using `[A-Za-z0-9][A-Za-z0-9_-]*`.
  - Use `/device/$DEVICE_ID/image.png` and
    `/api/device/$DEVICE_ID/config` first.
  - Fall back to `/weather.png` and `/api/config` to preserve old server
    compatibility.
  - Preserve local-first/public-fallback fetches, bearer-token handling, atomic
    image replacement, eips behavior, locks, timeouts, and logging rules.

- Existing tests are extended without deleting legacy coverage.

## Registry schema

`devices.json`:

```json
{
  "devices": [
    {
      "id": "default-kindle",
      "name": "Default Kindle",
      "type": "kindle_pw1",
      "resolution": [758, 1024],
      "enabled": true,
      "config_path": "devices/default-kindle/config.json",
      "image_path": "devices/default-kindle/image.png",
      "connection": {
        "host": "192.168.68.119",
        "user": "root",
        "ssh_profile": "kindle_dashboard",
        "port": 22
      }
    }
  ]
}
```

Rules:

- IDs match `^[a-z0-9][a-z0-9-]{0,63}$`.
- IDs are unique.
- Types are `kindle_pw1`, `esp32_epaper`, or `generic_png`.
- Width and height are integers from 64 through 4096.
- Config and image paths must be relative, remain under the project directory,
  and match the device ID folder.
- Kindle connections accept only `host`, `user`, `ssh_profile`, and optional
  `port`.
- ESP32 records may omit connection or later use a separately validated HTTP
  connection.
- Unknown fields, traversal paths, secrets, key paths, tokens, passwords, and
  known-hosts paths are rejected.

Server-side SSH profiles:

```python
SSH_PROFILES = {
    "kindle_dashboard": {
        "key_path": "/home/user/.ssh/kindle_dashboard_ed25519",
        "known_hosts": "/home/user/.ssh/kindle_dashboard_known_hosts",
        "options": ["-o", "StrictHostKeyChecking=yes"],
    },
}
```

## Rendering model

Introduce an explicit render context:

```python
RenderContext(
    device_id,
    device_type,
    resolution,
    config_path,
    output_path,
    notes_path,
    lock_path,
    state_path,
)
```

Theme renderers receive the config and context. The existing 758×1024 themes
remain pixel-compatible. In this phase:

- `kindle_pw1` supports all existing themes at 758×1024.
- `generic_png` may use a configurable resolution only when the selected
  renderer supports it; otherwise generation returns a clear validation error.
- `esp32_epaper` is registry/API-ready but BMP rendering returns `501`.

This avoids pretending that fixed-coordinate existing themes are safely
resolution-independent. General responsive e-paper layout is a later phase.

Freshness:

- Cached image must exist, be non-empty, and be newer than its config, registry,
  applicable notes file, and renderer source files.
- Maarif’s existing date/config rollover check remains active.
- A per-device lock prevents duplicate generation.
- Generation writes a temporary image and atomically replaces the cached image.

## Notes

`daily_notes.json` remains the global notes store. Each item may include:

```json
"devices": ["default-kindle", "kitchen-kindle"]
```

- Missing `devices` means visible on every device.
- Present `devices` must be a non-empty list of valid registered IDs.
- `get_active_reminders` accepts an optional `device_id` and filters before
  schedule evaluation.
- Daily Notes UI can assign devices, while existing notes remain unchanged and
  visible everywhere.

## HTTP APIs

Image server on port 8765:

- `GET|HEAD /weather.png`
  - Alias for `default-kindle`.
- `GET|HEAD /device/<device_id>/image.png`
  - `404` for invalid, unknown, or disabled devices.
  - `200 image/png` after freshness check/generation.
  - `503` on generation failure when no valid cached image exists.
- `GET|HEAD /device/<device_id>/image.bmp`
  - `501` for registered ESP32 devices; otherwise `404`.

Settings server on port 8767:

- `GET /api/devices`
  - Returns safe registry metadata only.
- `POST /api/devices`
  - CSRF-protected add operation.
- `GET /api/device/<device_id>/config`
  - Returns device type, resolution, theme, refresh/deep-sleep interval,
    Kindle frontlight where applicable, and image URL.
- `PUT /api/device/<device_id>`
  - CSRF-protected registry metadata edit.
- `POST /api/device/<device_id>/config`
  - CSRF-protected config save and regeneration.
- Existing `/api/config` remains the default-device alias.

The current `/api/device/status`, control, light, log, and push routes remain
default-device aliases. New device-qualified control routes are added without
removing the old ones.

## Settings UI

Add a Devices tab that:

- Lists enabled and disabled devices.
- Selects the active device.
- Adds a device with ID, name, type, resolution, and optional safe connection.
- Edits name, type, resolution, enabled state, and safe connection metadata.
- Loads the selected device’s config into the existing Location, Theme, Display,
  Notes, Device, Maintenance, and Status views.
- Saves and regenerates the selected device.
- Generates a selected-device preview.
- Pushes only to that selected device.

If a non-default Kindle has no connection, Push displays
`Push not configured for this device`. It never silently targets the default
Kindle. ESP32 and generic PNG controls hide Kindle-only actions.

## Migration and compatibility

On first registry load:

1. If `devices.json` is absent, create the default record.
2. If `devices/default-kindle/config.json` is absent, copy and validate
   `dashboard_config.json`; otherwise use current defaults.
3. If `devices/default-kindle/image.png` is absent and `kindle_weather.png`
   exists, copy it atomically.
4. Keep legacy config and image synchronized after successful default-device
   saves/renders:
   - default config → `dashboard_config.json`
   - default image → `kindle_weather.png`
5. Never overwrite an existing per-device file during migration.

This makes rollback possible by stopping the new code and continuing to use the
legacy files.

## Security and error handling

- Resolve all paths and verify they remain under the project root.
- Validate device IDs before filesystem access.
- Reject unknown registry/config fields.
- Use atomic JSON and image writes.
- Use per-device locks for config updates and rendering.
- Keep CSRF on every mutation.
- Use fixed SSH actions, argument arrays, timeouts, strict host checking, and
  profile allowlists.
- Do not log tokens, passwords, private key paths, known-hosts paths, or
  authorization headers.
- API errors contain safe messages only.

## Test and rollout strategy

Automated coverage:

- Registry loading, schema rejection, traversal rejection, and migration.
- Default compatibility aliases.
- Device rendering and isolated outputs.
- PNG endpoint success, freshness, HEAD, invalid/disabled IDs, and generation
  failure.
- Config API safe fields and type-specific values.
- Form/API selected-device saves.
- Kindle profile resolution and missing-connection behavior.
- Notes filtering by device.
- Devices tab and device selector.
- Existing full suite, public endpoint authorization, themes, Device Controls,
  Daily Notes, and Kindle script tests.

Deployment checkpoints:

1. Install registry and renderer while legacy endpoints still use legacy files.
2. Migrate `default-kindle` and verify byte-compatible aliases.
3. Enable new image/config routes.
4. Enable Devices UI and selected-device saves.
5. Install compatible Kindle scripts only after server routes pass.
6. Verify `/weather.png`, authenticated public `/weather.png`, current Kindle
   refresh, settings save, device controls, and timers.

Rollback:

- Restore the backed-up Python/scripts.
- Keep `dashboard_config.json` and `kindle_weather.png`, which stay synchronized.
- Restart existing services.
- Leave `devices.json` and `devices/` unused; no destructive reverse migration
  is needed.
