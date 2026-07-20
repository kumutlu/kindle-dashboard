"""Provider-driven, pure black-and-white Todo screen."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from providers.task_provider import TaskProvider
from themes.theme import Theme


FONT_BOLD = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)
FONT_REGULAR = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
)


def _font(paths, size):
    for path in paths:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


class TodoTheme(Theme):
    def __init__(self, task_provider, now_factory=None):
        if not isinstance(task_provider, TaskProvider):
            raise TypeError("task_provider must implement TaskProvider")
        self.task_provider = task_provider
        self.now_factory = now_factory or (
            lambda timezone_name: datetime.now(ZoneInfo(timezone_name))
        )

    @staticmethod
    def _ordered(tasks):
        return sorted(
            tasks,
            key=lambda task: (
                task.completed,
                task.sort_order,
                task.created_at,
                task.id,
            ),
        )

    def visible_tasks(self, device_id):
        return self._ordered(self.task_provider.list_tasks(device_id))[:8]

    @staticmethod
    def counts(tasks):
        remaining = sum(not task.completed for task in tasks)
        return remaining, len(tasks) - remaining

    @staticmethod
    def _truncate(draw, title, font, max_width):
        if draw.textbbox((0, 0), title, font=font)[2] <= max_width:
            return title
        ellipsis = "…"
        low, high = 0, len(title)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = title[:middle].rstrip() + ellipsis
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                low = middle
            else:
                high = middle - 1
        return title[:low].rstrip() + ellipsis

    def render(self, config, context):
        width, height = context.resolution
        if width < 400 or height < 600:
            raise ValueError("todo theme requires a portrait e-ink display")

        image = Image.new("1", (width, height), 1)
        draw = ImageDraw.Draw(image)
        wide = width >= 700
        margin = 54 if wide else 40
        heading_font = _font(FONT_BOLD, 54 if wide else 43)
        weekday_font = _font(FONT_BOLD, 27 if wide else 22)
        date_font = _font(FONT_REGULAR, 35 if wide else 28)
        task_font = _font(FONT_REGULAR, 30 if wide else 24)
        footer_font = _font(FONT_BOLD, 22 if wide else 18)

        timezone_name = config.get("timezone") or context.timezone
        now = self.now_factory(timezone_name)
        draw.text((margin, 46 if wide else 34), "Todo", fill=0, font=heading_font)
        date_top = 124 if wide else 102
        draw.text((margin, date_top), now.strftime("%A"), fill=0, font=weekday_font)
        draw.text(
            (margin, date_top + (34 if wide else 28)),
            now.strftime("%-d %B"),
            fill=0,
            font=date_font,
        )

        tasks = self._ordered(self.task_provider.list_tasks(context.device_id))
        visible = tasks[:8]
        remaining, completed = self.counts(tasks)
        footer_top = height - (145 if wide else 116)
        content_top = 235 if wide else 188
        available = footer_top - content_top - 12
        row_height = max(44, available // 8)

        if not visible:
            empty_font = _font(FONT_REGULAR, 34 if wide else 28)
            empty_text = "No tasks"
            box = draw.textbbox((0, 0), empty_text, font=empty_font)
            draw.text(
                ((width - (box[2] - box[0])) // 2, content_top + available // 3),
                empty_text,
                fill=0,
                font=empty_font,
            )
        else:
            symbol_width = 49 if wide else 40
            max_title_width = width - (margin * 2) - symbol_width
            for index, task in enumerate(visible):
                row_y = content_top + index * row_height
                marker_size = 25 if wide else 20
                marker_left = margin + 1
                marker_top = row_y + (7 if wide else 6)
                if task.completed:
                    stroke = 3 if wide else 2
                    draw.line(
                        (
                            marker_left + 2,
                            marker_top + marker_size // 2,
                            marker_left + marker_size // 3,
                            marker_top + marker_size - 3,
                        ),
                        fill=0,
                        width=stroke,
                    )
                    draw.line(
                        (
                            marker_left + marker_size // 3,
                            marker_top + marker_size - 3,
                            marker_left + marker_size - 1,
                            marker_top + 1,
                        ),
                        fill=0,
                        width=stroke,
                    )
                else:
                    draw.ellipse(
                        (
                            marker_left,
                            marker_top,
                            marker_left + marker_size,
                            marker_top + marker_size,
                        ),
                        outline=0,
                        width=3 if wide else 2,
                    )
                title = self._truncate(draw, task.title, task_font, max_title_width)
                draw.text(
                    (margin + symbol_width, row_y + (2 if wide else 1)),
                    title,
                    fill=0,
                    font=task_font,
                )

        draw.line((margin, footer_top, width - margin, footer_top), fill=0, width=2)
        draw.text(
            (margin, footer_top + (24 if wide else 18)),
            f"Remaining: {remaining}",
            fill=0,
            font=footer_font,
        )
        draw.text(
            (margin, footer_top + (60 if wide else 48)),
            f"Completed: {completed}",
            fill=0,
            font=footer_font,
        )
        return image
