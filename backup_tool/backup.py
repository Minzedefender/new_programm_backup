 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a//dev/null b/backup_tool/backup.py
index 0000000000000000000000000000000000000000..13bd8f2871f6a3475dd92e1a1b0d8f2a58e90f20 100644
--- a//dev/null
+++ b/backup_tool/backup.py
@@ -0,0 +1,316 @@
+"""Core backup logic for the 1C automation script."""
+from __future__ import annotations
+
+import logging
+import os
+import subprocess
+import zipfile
+from dataclasses import dataclass
+from datetime import datetime, timedelta
+from pathlib import Path
+from tempfile import TemporaryDirectory
+from typing import Dict, List, Optional, Sequence
+
+from .cloud import CloudUploadError, CloudUploader
+from .config import AppConfig, DatabaseConfig
+from .secrets import SecretManager, SecretNotFoundError
+from .utils import (
+    ensure_directory,
+    mask_sensitive,
+    render_template,
+    slugify,
+    timestamp_for_filename,
+)
+
+LOGGER = logging.getLogger(__name__)
+
+
+class BackupError(Exception):
+    """Raised when a backup operation fails."""
+
+
+@dataclass
+class BackupRunner:
+    config: AppConfig
+    secret_manager: SecretManager
+    cloud_uploader: CloudUploader
+    logger: logging.Logger = LOGGER
+
+    def run_all(self, only: Optional[Sequence[str]] = None) -> None:
+        """Execute backup for all configured databases.
+
+        Parameters
+        ----------
+        only:
+            Optional list of database names to process. When empty ``None`` all
+            databases from the configuration are processed.
+        """
+
+        names_filter = {name.lower() for name in only} if only else None
+        errors: List[str] = []
+        for database in self.config.databases:
+            if names_filter and database.name.lower() not in names_filter:
+                continue
+            try:
+                self.backup_database(database)
+            except Exception as exc:  # pragma: no cover - runtime safety
+                self.logger.exception("Ошибка при резервном копировании '%s': %s", database.name, exc)
+                errors.append(f"{database.name}: {exc}")
+        if errors:
+            raise BackupError("; ".join(errors))
+
+    # ------------------------------------------------------------------
+    def backup_database(self, database: DatabaseConfig) -> Path:
+        now = datetime.now()
+        timestamp = timestamp_for_filename(now)
+        safe_name = slugify(database.backup_prefix or database.name)
+        backup_dir = ensure_directory(Path(database.backup_directory).expanduser())
+        archive_path = backup_dir / f"{safe_name}_{timestamp}.zip"
+
+        context = self._base_context(database, now, backup_dir, archive_path, safe_name)
+        sensitive_keys: List[str] = []
+
+        self.logger.info("Начинаем резервное копирование базы '%s'.", database.name)
+        service_stopped = False
+        try:
+            if database.service.stop_command:
+                self._run_command(
+                    database.service.stop_command,
+                    timeout=database.service.stop_timeout,
+                    description="Остановка службы перед бэкапом",
+                )
+                service_stopped = True
+
+            if database.type == "file":
+                self._backup_file_database(database, archive_path)
+            elif database.type == "sql":
+                self._backup_sql_database(database, archive_path, context, sensitive_keys)
+            else:  # pragma: no cover - validation already handles
+                raise BackupError(f"Неизвестный тип базы: {database.type}")
+
+            self.logger.info("База '%s' успешно выгружена в файл '%s'.", database.name, archive_path)
+            self._enforce_retention(database, backup_dir, safe_name)
+
+            cloud_context = {key: value for key, value in context.items() if key not in sensitive_keys}
+            try:
+                self.cloud_uploader.upload(database.cloud, archive_path, cloud_context)
+            except CloudUploadError as exc:
+                self.logger.error("Ошибка выгрузки в облако для базы '%s': %s", database.name, exc)
+                raise
+
+            return archive_path
+        finally:
+            if service_stopped:
+                self._restart_service(database)
+
+    # ------------------------------------------------------------------
+    def _base_context(
+        self,
+        database: DatabaseConfig,
+        now: datetime,
+        backup_dir: Path,
+        archive_path: Path,
+        safe_name: str,
+    ) -> Dict[str, object]:
+        context = {
+            "name": database.name,
+            "safe_name": safe_name,
+            "source": database.source or "",
+            "backup_directory": str(backup_dir),
+            "backup_path": str(archive_path),
+            "backup_filename": archive_path.name,
+            "timestamp": timestamp_for_filename(now),
+            "timestamp_iso": now.replace(microsecond=0).isoformat(),
+            "year": now.strftime("%Y"),
+            "month": now.strftime("%m"),
+            "day": now.strftime("%d"),
+            "hour": now.strftime("%H"),
+            "minute": now.strftime("%M"),
+            "second": now.strftime("%S"),
+            "retention_days": database.retention_days,
+            "type": database.type,
+        }
+        return context
+
+    # ------------------------------------------------------------------
+    def _backup_file_database(self, database: DatabaseConfig, archive_path: Path) -> None:
+        if not database.source:
+            raise BackupError(f"Для базы '{database.name}' не указан путь source.")
+        source_path = Path(database.source).expanduser()
+        if not source_path.exists():
+            raise BackupError(f"Источник базы '{source_path}' не найден.")
+
+        self.logger.info("Архивирование файловой базы из '%s'.", source_path)
+        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
+            if source_path.is_file():
+                archive.write(source_path, arcname=source_path.name)
+            else:
+                base_name = source_path.name
+                for path in sorted(source_path.rglob("*")):
+                    arcname = Path(base_name) / path.relative_to(source_path)
+                    if path.is_dir():
+                        # zipfile does not create directory entries by default for empty dirs
+                        if not any(path.iterdir()):
+                            info = zipfile.ZipInfo(str(arcname) + "/")
+                            archive.writestr(info, "")
+                        continue
+                    archive.write(path, arcname=str(arcname))
+
+    # ------------------------------------------------------------------
+    def _backup_sql_database(
+        self,
+        database: DatabaseConfig,
+        archive_path: Path,
+        context: Dict[str, object],
+        sensitive_keys: List[str],
+    ) -> None:
+        sql_config = database.sql
+        if not sql_config.dump_command:
+            raise BackupError(
+                f"Для базы '{database.name}' не настроена команда выгрузки sql.dump_command."
+            )
+
+        dump_extension = sql_config.dump_extension or ".dt"
+        if not dump_extension.startswith("."):
+            dump_extension = "." + dump_extension
+
+        with TemporaryDirectory(prefix=f"backup_{context['safe_name']}_") as tmp_dir:
+            temp_path = Path(tmp_dir)
+            dump_file = temp_path / f"{context['safe_name']}{dump_extension}"
+            context.update(
+                {
+                    "sql_dump_dir": str(temp_path),
+                    "sql_dump_file": str(dump_file),
+                }
+            )
+            env = os.environ.copy()
+            secrets: List[str] = []
+
+            if sql_config.login_secret:
+                login = self._get_secret(sql_config.login_secret)
+                context["sql_login"] = login
+                sensitive_keys.append("sql_login")
+                secrets.append(login)
+            if sql_config.password_secret:
+                password = self._get_secret(sql_config.password_secret)
+                context["sql_password"] = password
+                sensitive_keys.append("sql_password")
+                secrets.append(password)
+            for env_name, secret_name in (sql_config.env_from_secrets or {}).items():
+                secret_value = self._get_secret(secret_name)
+                env[env_name] = secret_value
+                secrets.append(secret_value)
+
+            command = render_template(sql_config.dump_command, context)
+            masked_command = mask_sensitive(command, secrets)
+            self.logger.info("Запуск команды выгрузки SQL-базы: %s", masked_command)
+
+            result = subprocess.run(
+                command,
+                shell=True,
+                capture_output=True,
+                text=True,
+                cwd=sql_config.working_directory or None,
+                env=env,
+            )
+            if result.stdout:
+                self.logger.debug("STDOUT: %s", result.stdout.strip())
+            if result.stderr:
+                self.logger.warning("STDERR: %s", mask_sensitive(result.stderr.strip(), secrets))
+            if result.returncode != 0:
+                raise BackupError(
+                    f"Команда выгрузки завершилась с кодом {result.returncode}: {mask_sensitive(result.stderr.strip(), secrets)}"
+                )
+
+            produced_files = list(temp_path.glob("**/*"))
+            produced_files = [item for item in produced_files if item.is_file()]
+            if not produced_files:
+                raise BackupError("Команда выгрузки не создала файлов в целевой директории.")
+
+            self._zip_directory(temp_path, archive_path)
+
+    # ------------------------------------------------------------------
+    def _zip_directory(self, directory: Path, archive_path: Path) -> None:
+        directory = Path(directory)
+        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
+            for path in sorted(directory.rglob("*")):
+                if path.is_dir():
+                    continue
+                arcname = path.relative_to(directory)
+                archive.write(path, arcname=str(arcname))
+
+    # ------------------------------------------------------------------
+    def _enforce_retention(self, database: DatabaseConfig, backup_dir: Path, safe_name: str) -> None:
+        days = database.retention_days
+        if days is None or days <= 0:
+            return
+        cutoff = datetime.now() - timedelta(days=days)
+        prefix = f"{safe_name}_"
+        for file in backup_dir.glob("*.zip"):
+            if not file.is_file():
+                continue
+            if not file.name.startswith(prefix):
+                continue
+            if datetime.fromtimestamp(file.stat().st_mtime) < cutoff:
+                try:
+                    file.unlink()
+                    self.logger.info("Удалён устаревший бэкап '%s'.", file)
+                except OSError as exc:  # pragma: no cover - filesystem dependent
+                    self.logger.warning("Не удалось удалить файл '%s': %s", file, exc)
+
+    # ------------------------------------------------------------------
+    def _restart_service(self, database: DatabaseConfig) -> None:
+        service = database.service
+        command = service.start_command or service.restart_command
+        if not command:
+            self.logger.info("После бэкапа служба не перезапускается: команда не задана.")
+            return
+        try:
+            self._run_command(
+                command,
+                timeout=service.start_timeout,
+                description="Запуск службы после бэкапа",
+            )
+        except BackupError:
+            raise
+
+    # ------------------------------------------------------------------
+    def _run_command(
+        self,
+        command: str,
+        *,
+        timeout: Optional[int] = None,
+        description: Optional[str] = None,
+    ) -> None:
+        if not command:
+            return
+        desc = f" ({description})" if description else ""
+        self.logger.info("Выполнение команды%s: %s", desc, command)
+        try:
+            result = subprocess.run(
+                command,
+                shell=True,
+                capture_output=True,
+                text=True,
+                timeout=timeout,
+            )
+        except subprocess.TimeoutExpired as exc:
+            raise BackupError(f"Команда '{command}' превысила таймаут {timeout} секунд.") from exc
+        if result.stdout:
+            self.logger.debug("STDOUT: %s", result.stdout.strip())
+        if result.stderr:
+            self.logger.warning("STDERR: %s", result.stderr.strip())
+        if result.returncode != 0:
+            raise BackupError(
+                f"Команда '{command}' завершилась с кодом {result.returncode}: {result.stderr.strip()}"
+            )
+
+    # ------------------------------------------------------------------
+    def _get_secret(self, name: str) -> str:
+        try:
+            return self.secret_manager.get_secret(name)
+        except SecretNotFoundError as exc:
+            raise BackupError(f"Не найден секрет '{name}'.") from exc
+
+
+__all__ = ["BackupRunner", "BackupError"]
 
EOF
)