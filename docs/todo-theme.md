# Native Todo Theme

The `todo` theme displays a locally managed task list for one Kindle device.
It is independent from all weather data and does not change Kindle refresh or
delivery behavior.

## Use it

1. Open Settings and select the Kindle at the top of the page.
2. Open Appearance and select **Todo**.
3. Add, edit, complete, delete, or reorder tasks in the Todo List panel.
4. Save and regenerate the selected device configuration.

Task changes regenerate the selected device image immediately when that device
already uses the Todo theme. Tasks for weather devices can be prepared without
changing or regenerating their current image.

Incomplete and completed tasks are separate ordering groups. Completing a task
moves it to the end of the completed group; restoring it moves it to the end of
the incomplete group. Dragging and the move buttons never cross group
boundaries.

## Storage

Each device owns one file:

```text
devices/<device-id>/tasks.json
```

The file is created on the first mutation. A missing file is an empty list.
Writes are locked and atomically replaced. Back up the whole device directory
to preserve its configuration, tasks, cached image, and render state together.

Schema:

```json
{
  "version": 1,
  "tasks": [
    {
      "id": "9c7307bf-6c52-4af8-b7e0-bc07114c7864",
      "title": "Buy milk",
      "completed": false,
      "sort_order": 0,
      "created_at": "2026-07-20T09:30:00Z"
    }
  ]
}
```

Titles are stored in full up to 200 characters. The Kindle renderer truncates
only the displayed copy when necessary. `sort_order` is contiguous within each
completion group.

## HTTP API

All routes are scoped to a registered, enabled device:

```text
GET    /api/device/<device-id>/tasks
POST   /api/device/<device-id>/tasks
PUT    /api/device/<device-id>/tasks/<task-id>
DELETE /api/device/<device-id>/tasks/<task-id>
PUT    /api/device/<device-id>/tasks/reorder
```

Mutation requests use the Settings page’s existing `X-CSRF-Token` header.

Create body:

```json
{"title": "Buy milk"}
```

Update body (one or both fields):

```json
{"title": "Buy oat milk", "completed": true}
```

Reorder body contains every task ID in exactly one completion group:

```json
{
  "completed": false,
  "task_ids": [
    "9c7307bf-6c52-4af8-b7e0-bc07114c7864",
    "8f614f69-f27e-4d59-8058-a73269043fdb"
  ]
}
```

## Rendering

`ThemeRegistry` selects `TodoTheme` for the persisted `todo` ID. `TodoTheme`
receives a `TaskProvider`, requests normalized tasks by `device_id`, and returns
a Pillow image. It has no filesystem or settings-server dependency.

The layout supports 758×1024 and 600×800 portrait Kindles. It renders only
black and white, shows at most eight tasks with incomplete tasks first, and
shows counts for the entire list. An empty list displays `No tasks`.

## Adding another task provider

Implement `providers.task_provider.TaskProvider`:

```python
class ExampleTaskProvider(TaskProvider):
    def list_tasks(self, device_id):
        return [
            Task(
                id="provider-id",
                title="Example",
                completed=False,
                sort_order=0,
                created_at="2026-07-20T09:30:00Z",
            )
        ]
```

Inject that provider when constructing `TodoTheme`. Provider-specific
credentials, list selection, synchronization, and mutation UI remain outside
the theme. No Todo drawing code needs to change.
