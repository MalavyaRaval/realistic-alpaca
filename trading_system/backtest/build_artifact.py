"""Injects artifact_data.json into artifact_template.html to produce the
final artifact HTML - done as a file-level string substitution so the
(large) data payload never needs to pass through anything other than disk
I/O."""

from pathlib import Path

BASE = Path(__file__).resolve().parent

template = (BASE / "artifact_template.html").read_text(encoding="utf-8")
data_json = (BASE / "data" / "artifact_data.json").read_text(encoding="utf-8")

output = template.replace("__DATA_JSON__", data_json)

out_path = BASE / "data" / "artifact_final.html"
out_path.write_text(output, encoding="utf-8")
print(f"Wrote {out_path} ({len(output)} bytes)")
