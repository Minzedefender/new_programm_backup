 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a//dev/null b/backup_tool/cloud.py
index 0000000000000000000000000000000000000000..ec69544f14e4fe9163db0360c93845b79ae7b5a0 100644
--- a//dev/null
+++ b/backup_tool/cloud.py
@@ -0,0 +1,132 @@
+"""Cloud storage integrations used by the backup tool."""
+from __future__ import annotations
+
+import logging
+from dataclasses import dataclass
+from pathlib import Path
+from typing import Dict, Optional
+
+import requests
+
+from .config import CloudConfig
+from .secrets import SecretManager, SecretNotFoundError
+from .utils import render_template
+
+LOGGER = logging.getLogger(__name__)
+
+
+class CloudUploadError(Exception):
+    """Raised when uploading a file to cloud storage fails."""
+
+
+@dataclass
+class CloudUploader:
+    """Dispatch cloud uploads to the configured provider."""
+
+    secret_manager: SecretManager
+
+    def upload(self, cloud_config: CloudConfig, file_path: Path, context: Dict[str, object]) -> None:
+        if not cloud_config.enabled:
+            return
+        provider = (cloud_config.provider or "").lower()
+        if provider in {"yandex", "yandex_disk", "yandex-disk"}:
+            remote_path_template = cloud_config.remote_path or "/"
+            remote_path = render_template(remote_path_template, context) or "/"
+            uploader = YandexDiskUploader(
+                token=self._get_secret(cloud_config.token_secret),
+                login=self._get_secret(cloud_config.login_secret),
+                password=self._get_secret(cloud_config.password_secret),
+            )
+            uploader.upload(file_path, remote_path)
+        elif provider:
+            raise CloudUploadError(f"Облако '{provider}' не поддерживается.")
+        else:
+            raise CloudUploadError("Не задан провайдер облачного хранилища.")
+
+    def _get_secret(self, name: Optional[str]) -> Optional[str]:
+        if not name:
+            return None
+        try:
+            return self.secret_manager.get_secret(name)
+        except SecretNotFoundError as exc:
+            raise CloudUploadError(f"Секрет '{name}' не найден. Заполните его с помощью команды 'set-secret'.") from exc
+
+
+@dataclass
+class YandexDiskUploader:
+    token: Optional[str] = None
+    login: Optional[str] = None
+    password: Optional[str] = None
+
+    base_url: str = "https://webdav.yandex.ru"
+
+    def _headers(self) -> Dict[str, str]:
+        headers: Dict[str, str] = {}
+        if self.token:
+            headers["Authorization"] = f"OAuth {self.token}"
+        return headers
+
+    def _auth(self):
+        if self.login and self.password and not self.token:
+            return (self.login, self.password)
+        return None
+
+    def upload(self, file_path: Path, remote_directory: str) -> None:
+        file_path = Path(file_path)
+        if not file_path.exists():
+            raise CloudUploadError(f"Файл для выгрузки '{file_path}' не найден.")
+        normalized_dir = normalize_remote_directory(remote_directory)
+        ensure_remote_directory(self.base_url, normalized_dir, self._headers(), self._auth())
+        target_path = join_remote(normalized_dir, file_path.name)
+        with file_path.open("rb") as fh:
+            response = requests.put(
+                self.base_url + target_path,
+                data=fh,
+                headers=self._headers(),
+                auth=self._auth(),
+            )
+        if response.status_code not in {200, 201, 202, 204}:
+            raise CloudUploadError(
+                f"Ошибка выгрузки на Яндекс.Диск: HTTP {response.status_code} {response.text}"
+            )
+        LOGGER.info("Файл '%s' выгружен в облако по пути '%s'.", file_path, target_path)
+
+
+def normalize_remote_directory(path: str) -> str:
+    path = path.strip()
+    if path in {"", "/"}:
+        return "/"
+    return "/" + path.strip("/")
+
+
+def join_remote(directory: str, filename: str) -> str:
+    directory = normalize_remote_directory(directory)
+    if directory == "/":
+        return f"/{filename}"
+    return f"{directory}/{filename}"
+
+
+def ensure_remote_directory(base_url: str, directory: str, headers: Dict[str, str], auth) -> None:
+    if directory in {"", "/"}:
+        return
+    segments = [segment for segment in directory.strip("/").split("/") if segment]
+    current = ""
+    for segment in segments:
+        current = current + "/" + segment
+        response = requests.request(
+            "MKCOL",
+            base_url + current,
+            headers=headers,
+            auth=auth,
+        )
+        if response.status_code in {200, 201, 301, 405}:
+            continue
+        if response.status_code == 409:
+            # Parent directory missing; continue to next segment which will create it.
+            continue
+        raise CloudUploadError(
+            f"Не удалось создать папку '{current}' на Яндекс.Диске: HTTP {response.status_code} {response.text}"
+        )
+
+
+__all__ = ["CloudUploader", "CloudUploadError"]
 
EOF
)