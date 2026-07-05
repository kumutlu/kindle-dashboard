# Multi-Device Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the existing single-display dashboard into a registry-backed multi-device server without interrupting the current Kindle or legacy endpoints.

**Architecture:** Add a validated device registry and explicit per-device render context, then adapt the image server, settings server, device controls, notes, and Kindle scripts around those APIs. Legacy global files and routes remain synchronized aliases for `default-kindle`.

**Tech Stack:** Python 3 standard library, Pillow, `http.server`, JSON, POSIX/BusyBox shell, systemd, `unittest`.

---

## File map

**Create**

- `device_registry.py` — registry schema, safe path resolution, migration, public serialization.
- `device_renderer.py` — per-device rendering, freshness, locking, legacy synchronization.
- `test_device_registry.py` — registry and migration tests.
- `test_device_renderer.py` — render isolation, freshness, resolution, notes tests.
- `test_device_image_server.py` — per-device image route tests.
- Runtime only, ignored by Git: `devices.json`, `devices/<id>/config.json`, `devices/<id>/image.png`.

**Modify**

- `weather_image.py` — explicit render context/output and `--device` CLI.
- `serve_image.py` — device PNG/BMP endpoints and `/weather.png` alias.
- `settings_server.py` — device APIs, selected-device form state, Devices tab.
- `kindle_device.py` — safe SSH profiles and per-device connections.
- `public_image_server.py` — default-device resolution while preserving the protected route.
- `kindle_scripts/refresh.sh` — device ID and new-route-first fallback.
- `kindle_scripts/refresh-once.sh` — same compatibility behavior for one-shot refresh.
- `.gitignore` — ignore runtime registry/device artifacts and resolve existing conflict markers.
- Existing tests where fixtures or dependency injection need extension.

## Task 1: Establish a clean isolated baseline

**Files:** none

- [ ] **Step 1: Create an isolated worktree**

Use branch `codex/multi-device-dashboard`. Do not develop directly in the live
checkout. Copy no runtime secrets, `.env`, `run_dashboard.sh`, config, notes, or
generated images into the worktree.

- [ ] **Step 2: Record protected runtime hashes**

```bash
sha256sum \
  dashboard_config.json \
  daily_notes.json \
  run_dashboard.sh \
  public_image_server.py \
  kindle_scripts/refresh.sh \
  kindle_scripts/refresh-once.sh
```

Save values outside the repository for final comparison.

- [ ] **Step 3: Run the baseline suite**

```bash
python3 -m unittest discover
```

Expected: all existing tests pass. Stop and investigate if they do not.

## Task 2: Implement the device registry

**Files:**

- Create: `device_registry.py`
- Create: `test_device_registry.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing schema tests**

Cover:

```python
def test_missing_registry_creates_default_record_and_migrates_legacy_files()
def test_existing_device_files_are_not_overwritten()
def test_duplicate_or_invalid_device_id_is_rejected()
def test_traversal_paths_are_rejected()
def test_unknown_connection_or_secret_fields_are_rejected()
def test_public_record_never_contains_ssh_profile_secrets()
def test_disabled_and_unknown_devices_are_not_resolved_for_serving()
```

Use a temporary project root and known legacy config/image bytes.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest test_device_registry -v
```

Expected: import/function failures because `device_registry.py` does not exist.

- [ ] **Step 3: Add registry models and validation**

Implement immutable data objects:

```python
@dataclass(frozen=True)
class DeviceRecord:
    id: str
    name: str
    type: str
    resolution: tuple[int, int]
    enabled: bool
    config_path: Path
    image_path: Path
    connection: dict | None

class DeviceRegistry:
    def load(self) -> list[DeviceRecord]: ...
    def get(self, device_id, require_enabled=False) -> DeviceRecord: ...
    def add(self, candidate) -> DeviceRecord: ...
    def update(self, device_id, candidate) -> DeviceRecord: ...
    def public_records(self) -> list[dict]: ...
    def ensure_default_migration(self) -> DeviceRecord: ...
```

Constants:

```python
DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DEVICE_TYPES = {"kindle_pw1", "esp32_epaper", "generic_png"}
CONNECTION_FIELDS = {"host", "user", "ssh_profile", "port", "method"}
FORBIDDEN_CONNECTION_FIELDS = {
    "key_path", "known_hosts", "password", "token", "private_key",
}
```

Resolve paths with `Path.resolve()` and require
`resolved.is_relative_to(project_root.resolve())`. Enforce
`devices/<device_id>/config.json` and `devices/<device_id>/image.png`.

- [ ] **Step 4: Add atomic registry and migration writes**

Write JSON to a sibling temporary file, `fsync`, and `os.replace`. Migration:

```python
if not devices_json.exists():
    write_registry({"devices": [default_record()]})
if not default_config.exists():
    copy validated legacy config or DEFAULT_CONFIG
if not default_image.exists() and legacy_image.exists():
    atomic_copy(legacy_image, default_image)
```

- [ ] **Step 5: Repair `.gitignore`**

Remove the existing merge-conflict markers and retain all intended ignores.
Add:

```gitignore
devices.json
devices/
```

- [ ] **Step 6: Verify GREEN**

```bash
python3 -m unittest test_device_registry -v
git diff --check
```

Expected: all registry tests pass; no conflict markers or whitespace errors.

## Task 3: Make rendering explicit and device-aware

**Files:**

- Create: `device_renderer.py`
- Create: `test_device_renderer.py`
- Modify: `weather_image.py`
- Modify: renderer/theme tests as required

- [ ] **Step 1: Write failing renderer tests**

Cover:

```python
def test_render_default_device_writes_device_and_legacy_images()
def test_render_named_device_does_not_modify_default_or_legacy_images()
def test_render_uses_selected_device_config()
def test_fresh_image_is_reused_without_rendering()
def test_changed_config_forces_regeneration()
def test_each_device_uses_its_own_lock_and_render_state()
def test_fixed_layout_rejects_unsupported_resolution()
def test_cli_without_device_targets_default()
def test_cli_device_targets_named_device()
```

Mock weather, Pi-hole, Tailscale, prayer, and system metrics. Never call live
services in unit tests.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest test_device_renderer -v
```

- [ ] **Step 3: Introduce render context**

In `weather_image.py`:

```python
@dataclass(frozen=True)
class RenderContext:
    device_id: str
    device_type: str
    resolution: tuple[int, int]
    config_path: Path
    output_path: Path
    notes_path: Path
    lock_path: Path
    state_path: Path
```

Make `save_dashboard(img, data, context=None)` use
`context.output_path` and preserve `OUT` only as a legacy default for existing
direct renderer tests.

Make theme renderers accept `context=None`. Validate existing themes at
`(758, 1024)` in this phase; return a clear error for unsupported dimensions.

- [ ] **Step 4: Implement renderer service**

```python
def render_device(device_id="default-kindle", force=False, registry=None):
    device = registry.get(device_id, require_enabled=True)
    config = load_config(device.config_path)
    context = context_for(device)
    if not force and image_is_fresh(device, config, context):
        return device.image_path
    with device_lock(context.lock_path):
        render_dashboard(config, context)
        write_render_state(context.state_path, config)
        if device_id == "default-kindle":
            sync_legacy_config_and_image(device, config)
    return device.image_path
```

Source/config/notes/registry mtimes and Maarif rollover participate in
freshness.

- [ ] **Step 5: Add CLI compatibility**

Use `argparse`:

```python
parser.add_argument("--device", default="default-kindle")
parser.add_argument("--force", action="store_true")
```

`python3 weather_image.py` remains the default-device command.

- [ ] **Step 6: Verify renderer and legacy suites**

```bash
python3 -m unittest \
  test_device_renderer \
  test_weather_themes \
  test_weather_visibility \
  test_compact_dashboard \
  test_family_dashboard \
  test_maarif_reliability -v
```

## Task 4: Add device-scoped notes

**Files:**

- Modify: `weather_image.py`
- Modify: `settings_server.py`
- Modify: `test_family_dashboard.py`
- Modify: `test_settings_server.py`

- [ ] **Step 1: Write failing filtering tests**

```python
def test_note_without_devices_is_visible_to_all_devices()
def test_scoped_note_is_visible_only_to_listed_devices()
def test_invalid_or_empty_devices_list_is_rejected_on_save()
def test_existing_notes_payload_remains_backward_compatible()
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest \
  test_family_dashboard.FamilyDashboardTests.test_scoped_note_is_visible_only_to_listed_devices \
  test_settings_server.SettingsServerTests.test_invalid_note_devices_are_rejected -v
```

- [ ] **Step 3: Implement filtering**

Extend:

```python
def get_active_reminders(notes_data, local_date_str, device_id=None):
    ...
    targets = item.get("devices")
    if targets is not None and device_id not in targets:
        continue
```

Validate optional `devices` as a non-empty unique list of registered IDs.
Pass the render context device ID from Family Dashboard.

- [ ] **Step 4: Add assignment controls to Daily Notes**

Render safe device checkboxes from public registry metadata. Missing selections
mean all devices. Existing notes are not rewritten until the user saves them.

- [ ] **Step 5: Verify GREEN**

```bash
python3 -m unittest test_family_dashboard test_settings_server -v
```

## Task 5: Add per-device image endpoints

**Files:**

- Modify: `serve_image.py`
- Create: `test_device_image_server.py`
- Modify: legacy image server tests if present

- [ ] **Step 1: Write failing route tests**

Cover GET and HEAD:

```python
def test_default_device_png_endpoint_returns_png()
def test_weather_png_is_default_device_alias()
def test_invalid_unknown_or_disabled_device_is_404()
def test_stale_image_regenerates_once()
def test_fresh_image_does_not_regenerate()
def test_generation_failure_without_cache_is_503()
def test_esp32_bmp_placeholder_is_501()
def test_non_esp32_bmp_is_404()
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest test_device_image_server -v
```

- [ ] **Step 3: Implement strict routing**

Parse only:

```python
DEVICE_PNG_RE = re.compile(r"^/device/([a-z0-9][a-z0-9-]{0,63})/image\\.png$")
DEVICE_BMP_RE = re.compile(r"^/device/([a-z0-9][a-z0-9-]{0,63})/image\\.bmp$")
```

Inject `registry` and `render_device` into `make_server()` for testing. Return
`Content-Type`, `Content-Length`, `Cache-Control: no-store`, and
`X-Content-Type-Options: nosniff`.

- [ ] **Step 4: Preserve the battery and legacy aliases**

Only `/weather.png` accepts `?batt=N`; it resolves to `default-kindle`.
Do not add directory listing or arbitrary file serving.

- [ ] **Step 5: Verify GREEN**

```bash
python3 -m unittest test_device_image_server -v
```

## Task 6: Add device config APIs and selected-device settings

**Files:**

- Modify: `settings_server.py`
- Modify: `test_settings_server.py`

- [ ] **Step 1: Write failing API tests**

Cover:

```python
def test_devices_api_returns_only_safe_metadata()
def test_device_config_api_returns_type_specific_public_config()
def test_invalid_device_config_route_is_404()
def test_add_and_update_device_require_csrf()
def test_secret_connection_fields_are_rejected()
def test_selected_device_form_save_updates_only_selected_config()
def test_legacy_api_config_updates_default_device()
def test_settings_html_includes_devices_tab_and_active_device()
def test_selected_device_preview_uses_device_image_url()
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest test_settings_server -v
```

- [ ] **Step 3: Add qualified APIs**

Implement:

```text
GET  /api/devices
POST /api/devices
GET  /api/device/<id>/config
PUT  /api/device/<id>
POST /api/device/<id>/config
```

Mutations require the existing CSRF header. Responses include only:

```json
{
  "id": "default-kindle",
  "name": "Default Kindle",
  "type": "kindle_pw1",
  "resolution": [758, 1024],
  "enabled": true,
  "connection": {
    "host": "192.168.68.119",
    "user": "root",
    "ssh_profile": "kindle_dashboard",
    "port": 22
  },
  "theme": "family_dashboard",
  "refresh_interval_minutes": 60,
  "kindle_frontlight": 8,
  "image_url": "/device/default-kindle/image.png"
}
```

Never include profile internals.

- [ ] **Step 4: Make existing routes aliases**

`GET|POST /api/config` and unqualified `/settings` saves map to
`default-kindle` unless `device_id` is explicitly submitted. Preserve current
CSRF, rollback-on-generation-failure, and status redirects.

- [ ] **Step 5: Add Devices tab**

Add:

- device list and active-device selector;
- add/edit form for ID, name, type, width, height, enabled, host, user,
  SSH profile, and port;
- selected-device preview;
- hidden `device_id` in the main settings form;
- API-driven config reload when selection changes.

Do not redesign the existing tabs.

- [ ] **Step 6: Verify GREEN**

```bash
python3 -m unittest test_settings_server -v
```

## Task 7: Make Kindle controls device-aware

**Files:**

- Modify: `kindle_device.py`
- Modify: `settings_server.py`
- Modify: `test_kindle_device.py`
- Modify: `test_settings_server.py`

- [ ] **Step 1: Write failing connection tests**

Cover:

```python
def test_profile_builds_strict_ssh_arguments_without_shell()
def test_api_never_exposes_profile_paths()
def test_named_kindle_uses_its_connection_host_user_and_port()
def test_missing_named_connection_does_not_fall_back()
def test_default_kindle_may_use_legacy_connection_fallback()
def test_non_kindle_rejects_kindle_actions()
def test_push_renders_and_refreshes_selected_device()
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest test_kindle_device test_settings_server -v
```

- [ ] **Step 3: Add server-side profile mapping**

```python
SSH_PROFILES = {
    "kindle_dashboard": {
        "key_path": Path("/home/user/.ssh/kindle_dashboard_ed25519"),
        "known_hosts": Path(
            "/home/user/.ssh/kindle_dashboard_known_hosts"
        ),
        "options": ("-o", "StrictHostKeyChecking=yes"),
    },
}
```

Build `ssh` argv from validated record fields. Keep `shell=False`, fixed remote
commands, `BatchMode`, `IdentitiesOnly`, timeout, and log suppression.

- [ ] **Step 4: Qualify control routes**

Add device-qualified control routes while retaining current aliases for
`default-kindle`. Push calls `render_device(selected_id, force=True)` then the
fixed refresh command on that device.

- [ ] **Step 5: Verify GREEN**

```bash
python3 -m unittest test_kindle_device test_settings_server -v
```

## Task 8: Update Kindle scripts with server fallback

**Files:**

- Modify: `kindle_scripts/refresh.sh`
- Modify: `kindle_scripts/refresh-once.sh`
- Add or modify shell-script tests

- [ ] **Step 1: Write failing static/behavior tests**

Verify both scripts:

- read `/mnt/us/dashboard/device-id`;
- default to `default-kindle`;
- reject invalid IDs;
- try qualified image/config routes first;
- fall back to `/weather.png` and `/api/config`;
- preserve public bearer fallback without putting the token in URLs/logs;
- preserve atomic download, lock, timeout, eips, and refresh-loop behavior.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest discover -p 'test*kindle*py' -v
```

- [ ] **Step 3: Implement compatible route selection**

BusyBox-compatible pattern:

```sh
DEVICE_ID=default-kindle
if [ -s /mnt/us/dashboard/device-id ]; then
    CANDIDATE=$(sed -n '1p' /mnt/us/dashboard/device-id)
    case "$CANDIDATE" in
        *[!a-z0-9-]*|"") ;;
        *) DEVICE_ID=$CANDIDATE ;;
    esac
fi

DEVICE_IMAGE_URL="http://192.168.68.167:8765/device/$DEVICE_ID/image.png"
DEVICE_CONFIG_URL="http://192.168.68.167:8767/api/device/$DEVICE_ID/config"
```

Use qualified routes first, legacy routes second, public URL last.

- [ ] **Step 4: Verify GREEN**

```bash
python3 -m unittest discover -p 'test*kindle*py' -v
sh -n kindle_scripts/refresh.sh
sh -n kindle_scripts/refresh-once.sh
```

Do not install scripts on the Kindle until all Ubuntu route checks pass.

## Task 9: Preserve the protected public endpoint

**Files:**

- Modify: `public_image_server.py`
- Modify: `test_public_image_server.py`

- [ ] **Step 1: Write failing compatibility tests**

Verify authenticated `/weather.png` resolves the default device while:

- missing/wrong bearer remains `403`;
- `/`, source files, and device-qualified paths remain `404`;
- token never appears in logs;
- bytes match default device image.

- [ ] **Step 2: Implement default-device lookup**

Inject `render_device("default-kindle")` and read only its resulting PNG.
Do not expose `/device/<id>` through Funnel in this phase.

- [ ] **Step 3: Verify GREEN**

```bash
python3 -m unittest test_public_image_server -v
```

## Task 10: Full verification and controlled deployment

**Files:** all changed files

- [ ] **Step 1: Run the complete suite**

```bash
git diff --check
python3 -m unittest discover
```

Expected: zero failures/errors.

- [ ] **Step 2: Generate the default device**

```bash
python3 weather_image.py --device default-kindle --force
python3 weather_image.py
file devices/default-kindle/image.png
file kindle_weather.png
```

Expected: both are 758×1024 grayscale PNGs.

- [ ] **Step 3: Verify HTTP routes locally**

```bash
curl -fsSI http://127.0.0.1:8765/weather.png
curl -fsSI \
  http://127.0.0.1:8765/device/default-kindle/image.png
curl -fsS \
  http://127.0.0.1:8767/api/device/default-kindle/config
curl -fsS http://127.0.0.1:8767/settings
```

Verify invalid IDs return `404`, public `/weather.png` remains `403` without a
token, and no response contains SSH profile internals.

- [ ] **Step 4: Verify UI behavior**

In the browser:

1. Open Settings.
2. Confirm Default Kindle in Devices.
3. Select it and change theme.
4. Save & Regenerate.
5. Confirm only its config changed.
6. Confirm preview and `/weather.png` show the same default image.
7. Restore the original theme/config if the check changed it unintentionally.

- [ ] **Step 5: Deploy Kindle script compatibility**

Back up the live Kindle scripts first. Install only the two verified scripts.
Leave `/mnt/us/dashboard/device-id` absent so the current Kindle defaults to
`default-kindle`. Run one-shot refresh, verify the new local route was used,
and verify exactly one refresh daemon remains.

- [ ] **Step 6: Recheck protected files and services**

Compare protected hashes. Expected changes are limited to the explicitly
approved Kindle scripts. Verify:

```bash
systemctl is-active \
  kindle-dashboard-server.service \
  kindle-dashboard-settings.service \
  kindle-dashboard-public.service
systemctl is-active kindle-dashboard-generate.timer
```

- [ ] **Step 7: Commit and push once**

Do not add runtime config, device registry, generated images, notes, logs,
backups, caches, secrets, or screenshots.

```bash
git status --short
git diff --stat
git add \
  .gitignore \
  device_registry.py \
  device_renderer.py \
  weather_image.py \
  serve_image.py \
  settings_server.py \
  kindle_device.py \
  public_image_server.py \
  kindle_scripts/refresh.sh \
  kindle_scripts/refresh-once.sh \
  test_device_registry.py \
  test_device_renderer.py \
  test_device_image_server.py \
  test_weather_themes.py \
  test_weather_visibility.py \
  test_compact_dashboard.py \
  test_family_dashboard.py \
  test_maarif_reliability.py \
  test_settings_server.py \
  test_kindle_device.py \
  test_public_image_server.py
git commit -m "Add multi-device dashboard architecture"
git push origin main
```

- [ ] **Step 8: Report evidence**

Report commit hash, changed files, test count, endpoint results, default Kindle
refresh result, service status, and intentionally untracked runtime files.
