 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a//dev/null b/backup_tool/utils.py
index 0000000000000000000000000000000000000000..3595dda9b9f3dd01b85177703d3eb2c6a052e495 100644
--- a//dev/null
+++ b/backup_tool/utils.py
@@ -0,0 +1,80 @@
+"""Helper utilities for the 1C backup automation tool."""
+from __future__ import annotations
+
+import re
+from datetime import datetime
+from pathlib import Path
+from typing import Dict, Iterable, Optional
+
+
+def slugify(value: str, fallback: str = "backup") -> str:
+    """Return a filesystem-friendly version of *value*.
+
+    The function keeps latin/cyrillic letters, numbers and underscores. Other
+    characters are replaced with underscores.
+    """
+
+    value = value.strip()
+    # Replace any sequence of characters that is not a word character with '_'
+    sanitized = re.sub(r"[^0-9A-Za-zА-Яа-я_]+", "_", value)
+    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
+    return sanitized or fallback
+
+
+def ensure_directory(path: Path) -> Path:
+    """Create *path* if it does not exist and return it."""
+
+    path = Path(path)
+    path.mkdir(parents=True, exist_ok=True)
+    return path
+
+
+def timestamp_for_filename(dt: Optional[datetime] = None) -> str:
+    dt = dt or datetime.now()
+    return dt.strftime("%Y_%m_%d_%H_%M_%S")
+
+
+class StrictTemplateDict(dict):
+    """Dictionary for safe :py:meth:`str.format_map` usage."""
+
+    def __missing__(self, key: str):  # pragma: no cover - small helper
+        raise KeyError(key)
+
+
+def render_template(template: Optional[str], context: Dict[str, object]) -> Optional[str]:
+    """Render a template string using :py:meth:`str.format` syntax."""
+
+    if template in (None, ""):
+        return template
+    try:
+        return template.format_map(StrictTemplateDict({k: _stringify(v) for k, v in context.items()}))
+    except KeyError as exc:
+        missing = exc.args[0]
+        raise KeyError(
+            f"В шаблоне '{template}' отсутствует переменная '{missing}'."
+        ) from exc
+
+
+def _stringify(value: object) -> str:
+    if isinstance(value, Path):
+        return str(value)
+    return value  # type: ignore[return-value]
+
+
+def mask_sensitive(value: str, secrets: Iterable[str]) -> str:
+    """Replace occurrences of secret values in *value* with '***'."""
+
+    masked = value
+    for secret in secrets:
+        if secret:
+            masked = masked.replace(secret, "***")
+    return masked
+
+
+__all__ = [
+    "slugify",
+    "ensure_directory",
+    "timestamp_for_filename",
+    "render_template",
+    "mask_sensitive",
+]
 
EOF
)