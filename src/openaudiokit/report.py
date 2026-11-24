"""Serialize reports as strict JSON, spreadsheet-safe CSV or escaped HTML."""

import csv
import io
import json
from collections.abc import Iterator
from dataclasses import asdict, is_dataclass
from html import escape
from pathlib import Path
from typing import Any

from .audio import AudioPath, atomic_output
from .errors import AudioIOError, ValidationError


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported report value: {type(value).__name__}")


def _leaves(value: Any, key: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict) and value:
        for child, item in value.items():
            yield from _leaves(item, f"{key}.{child}" if key else child)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from _leaves(item, f"{key}[{index}]")
    else:
        yield key or "$", value


def _cell(value: Any, *, spreadsheet: bool = False) -> str:
    if isinstance(value, str):
        if spreadsheet and (
            value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
        ):
            return "'" + value
        return value
    return json.dumps(value, ensure_ascii=False)


def render_report(data: Any, format: str = "json") -> str:
    """Render JSON-compatible objects, paths or report dataclasses."""
    kind = format.lower()
    if kind not in {"json", "csv", "html"}:
        raise ValidationError(f"Unknown report format: {format!r}.")
    try:
        serialized = json.dumps(
            data, default=_json_default, allow_nan=False, ensure_ascii=False, indent=2
        )
    except (ValueError, TypeError) as exc:
        raise ValidationError("Report contains a value that cannot be serialized.") from exc
    if kind == "json":
        return serialized + "\n"
    rows = _leaves(json.loads(serialized))
    if kind == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(("field", "value"))
        writer.writerows(
            (_cell(key, spreadsheet=True), _cell(value, spreadsheet=True)) for key, value in rows
        )
        return buffer.getvalue()
    body = "\n".join(
        f'<tr><th scope="row">{escape(key)}</th><td>{escape(_cell(value))}</td></tr>'
        for key, value in rows
    )
    return f"""<!DOCTYPE html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>OpenAudioKit Report</title>
<style>
body {{font: 16px system-ui; margin: 2rem auto; max-width: 68rem; padding: 0 1rem;}}
table {{border-collapse: collapse; width: 100%;}}
th, td {{padding: .5rem 1rem; border-bottom: 1px solid #c6ccd6; text-align: left; overflow-wrap: anywhere;}}
</style>
<h1>OpenAudioKit Report</h1>
<table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>
{body}
</tbody></table></html>
"""


def write_report(destination: AudioPath, data: Any, format: str | None = None) -> None:
    """Write a UTF-8 report atomically, inferring its format from the extension."""
    path = Path(destination)
    rendered = render_report(data, format or path.suffix.lstrip("."))
    try:
        with (
            atomic_output(path) as temporary,
            temporary.open("w", encoding="utf-8", newline="") as stream,
        ):
            stream.write(rendered)
    except OSError as exc:
        raise AudioIOError(f"Could not write report: {path}") from exc
