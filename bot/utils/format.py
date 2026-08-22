# Developed by ARGON telegram: @REACTIVEARGON
"""Shared human-readable formatting helpers."""


def humanbytes(size) -> str:
    if not size:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.2f} {units[i]}"


def format_time(milliseconds: int) -> str:
    if milliseconds <= 0:
        return "0s"

    seconds = int(milliseconds) // 1000
    minutes, secs = divmod(seconds, 60)
    hours, mins = divmod(minutes, 60)
    days, hrs = divmod(hours, 24)

    if days > 0:
        return f"{days}d {hrs}h"
    if hours > 0:
        return f"{hours}h {mins:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


# Backwards-compatible alias (original camelCase name used across the repo)
TimeFormatter = format_time
