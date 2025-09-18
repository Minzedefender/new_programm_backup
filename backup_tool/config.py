 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a//dev/null b/backup_tool/config.py
index 0000000000000000000000000000000000000000..8e2a96681d25adefedfa16de5f7e7ad2d393f76b 100644
--- a//dev/null
+++ b/backup_tool/config.py
@@ -0,0 +1,278 @@
+"""Configuration models and helpers for the backup tool."""
+from __future__ import annotations
+
+from dataclasses import dataclass, field
+from pathlib import Path
+from typing import Dict, List, Optional
+
+import yaml
+
+CONFIG_FILENAME = "config.yaml"
+
+
+class ConfigError(Exception):
+    """Raised when configuration loading or validation fails."""
+
+
+@dataclass
+class CloudConfig:
+    enabled: bool = False
+    provider: Optional[str] = None
+    remote_path: Optional[str] = None
+    token_secret: Optional[str] = None
+    login_secret: Optional[str] = None
+    password_secret: Optional[str] = None
+    extra: Dict[str, str] = field(default_factory=dict)
+
+    @classmethod
+    def from_dict(cls, data: Optional[Dict]) -> "CloudConfig":
+        data = data or {}
+        known = {
+            "enabled": bool(data.get("enabled", False)),
+            "provider": data.get("provider"),
+            "remote_path": data.get("remote_path"),
+            "token_secret": data.get("token_secret"),
+            "login_secret": data.get("login_secret"),
+            "password_secret": data.get("password_secret"),
+        }
+        extra = {
+            key: value
+            for key, value in data.items()
+            if key not in known
+        }
+        return cls(extra=extra, **known)
+
+    def to_dict(self) -> Dict:
+        result: Dict[str, object] = {
+            "enabled": self.enabled,
+            "provider": self.provider,
+            "remote_path": self.remote_path,
+            "token_secret": self.token_secret,
+            "login_secret": self.login_secret,
+            "password_secret": self.password_secret,
+        }
+        result.update(self.extra)
+        # remove None values for cleaner YAML
+        return {key: value for key, value in result.items() if value is not None}
+
+
+@dataclass
+class ServiceControl:
+    stop_command: Optional[str] = None
+    start_command: Optional[str] = None
+    restart_command: Optional[str] = None
+    stop_timeout: Optional[int] = None
+    start_timeout: Optional[int] = None
+
+    @classmethod
+    def from_dict(cls, data: Optional[Dict]) -> "ServiceControl":
+        data = data or {}
+        return cls(
+            stop_command=data.get("stop_command"),
+            start_command=data.get("start_command"),
+            restart_command=data.get("restart_command"),
+            stop_timeout=_safe_int(data.get("stop_timeout")),
+            start_timeout=_safe_int(data.get("start_timeout")),
+        )
+
+    def to_dict(self) -> Dict:
+        result: Dict[str, object] = {
+            "stop_command": self.stop_command,
+            "start_command": self.start_command,
+            "restart_command": self.restart_command,
+            "stop_timeout": self.stop_timeout,
+            "start_timeout": self.start_timeout,
+        }
+        return {key: value for key, value in result.items() if value is not None}
+
+
+@dataclass
+class SQLConfig:
+    dump_command: Optional[str] = None
+    working_directory: Optional[str] = None
+    dump_extension: str = ".dt"
+    login_secret: Optional[str] = None
+    password_secret: Optional[str] = None
+    env_from_secrets: Dict[str, str] = field(default_factory=dict)
+    extra: Dict[str, str] = field(default_factory=dict)
+
+    @classmethod
+    def from_dict(cls, data: Optional[Dict]) -> "SQLConfig":
+        data = data or {}
+        known = {
+            "dump_command": data.get("dump_command"),
+            "working_directory": data.get("working_directory"),
+            "dump_extension": data.get("dump_extension", ".dt"),
+            "login_secret": data.get("login_secret"),
+            "password_secret": data.get("password_secret"),
+            "env_from_secrets": data.get("env_from_secrets", {}),
+        }
+        extra = {
+            key: value
+            for key, value in data.items()
+            if key not in known
+        }
+        return cls(extra=extra, **known)
+
+    def to_dict(self) -> Dict:
+        result: Dict[str, object] = {
+            "dump_command": self.dump_command,
+            "working_directory": self.working_directory,
+            "dump_extension": self.dump_extension,
+            "login_secret": self.login_secret,
+            "password_secret": self.password_secret,
+            "env_from_secrets": self.env_from_secrets,
+        }
+        result.update(self.extra)
+        return {key: value for key, value in result.items() if value is not None and value != {}}
+
+
+@dataclass
+class DatabaseConfig:
+    name: str
+    type: str
+    source: Optional[str] = None
+    backup_directory: str = "backups"
+    backup_prefix: Optional[str] = None
+    retention_days: int = 7
+    cloud: CloudConfig = field(default_factory=CloudConfig)
+    service: ServiceControl = field(default_factory=ServiceControl)
+    sql: SQLConfig = field(default_factory=SQLConfig)
+    extra: Dict[str, str] = field(default_factory=dict)
+
+    def validate(self) -> None:
+        if not self.name:
+            raise ConfigError("Поле 'name' не может быть пустым.")
+        if self.type not in {"file", "sql"}:
+            raise ConfigError(
+                f"Неверный тип базы '{self.type}'. Используйте 'file' или 'sql'."
+            )
+        if not self.backup_directory:
+            raise ConfigError(
+                f"Для базы '{self.name}' не задан путь для сохранения бэкапов."
+            )
+        if self.type == "file" and not self.source:
+            raise ConfigError(
+                f"Для файловой базы '{self.name}' необходимо указать 'source'."
+            )
+        if self.retention_days is not None and self.retention_days < 0:
+            raise ConfigError(
+                f"Поле retention_days для базы '{self.name}' должно быть неотрицательным."
+            )
+        if self.type == "sql" and not self.sql.dump_command:
+            raise ConfigError(
+                f"Для SQL-базы '{self.name}' необходимо указать sql.dump_command."
+            )
+
+    @classmethod
+    def from_dict(cls, data: Dict) -> "DatabaseConfig":
+        if "name" not in data:
+            raise ConfigError("Каждый объект базы данных должен содержать поле 'name'.")
+        backup_directory = data.get("backup_directory") or data.get("backup_dir")
+        known_keys = {
+            "name",
+            "type",
+            "source",
+            "backup_directory",
+            "backup_dir",
+            "backup_prefix",
+            "retention_days",
+            "cloud",
+            "service",
+            "sql",
+        }
+        extra = {key: value for key, value in data.items() if key not in known_keys}
+        config = cls(
+            name=data["name"],
+            type=data.get("type", "file"),
+            source=data.get("source"),
+            backup_directory=backup_directory or "backups",
+            backup_prefix=data.get("backup_prefix"),
+            retention_days=_safe_int(data.get("retention_days"), default=7),
+            cloud=CloudConfig.from_dict(data.get("cloud")),
+            service=ServiceControl.from_dict(data.get("service")),
+            sql=SQLConfig.from_dict(data.get("sql")),
+            extra=extra,
+        )
+        config.validate()
+        return config
+
+    def to_dict(self) -> Dict:
+        result: Dict[str, object] = {
+            "name": self.name,
+            "type": self.type,
+            "source": self.source,
+            "backup_directory": self.backup_directory,
+            "backup_prefix": self.backup_prefix,
+            "retention_days": self.retention_days,
+            "cloud": self.cloud.to_dict(),
+            "service": self.service.to_dict(),
+            "sql": self.sql.to_dict(),
+        }
+        result.update(self.extra)
+        # remove empty nested dicts
+        cleaned: Dict[str, object] = {}
+        for key, value in result.items():
+            if value in (None, {}, []):
+                continue
+            cleaned[key] = value
+        return cleaned
+
+
+@dataclass
+class AppConfig:
+    databases: List[DatabaseConfig] = field(default_factory=list)
+
+    def to_dict(self) -> Dict:
+        return {"databases": [db.to_dict() for db in self.databases]}
+
+    def add_database(self, config: DatabaseConfig) -> None:
+        self.databases.append(config)
+
+
+# ---------------------------------------------------------------------------
+def _safe_int(value, default: Optional[int] = None) -> Optional[int]:
+    if value is None:
+        return default
+    try:
+        return int(value)
+    except (TypeError, ValueError):  # pragma: no cover - validation
+        raise ConfigError(f"Значение '{value}' не может быть преобразовано в целое число.")
+
+
+# ---------------------------------------------------------------------------
+def load_config(path: Path = Path(CONFIG_FILENAME)) -> AppConfig:
+    path = Path(path)
+    if not path.exists():
+        return AppConfig()
+    data = yaml.safe_load(path.read_text(encoding="utf-8"))
+    if not data:
+        return AppConfig()
+    if "databases" not in data or not isinstance(data["databases"], list):
+        raise ConfigError("Файл конфигурации должен содержать ключ 'databases'.")
+    databases = [DatabaseConfig.from_dict(item) for item in data["databases"]]
+    return AppConfig(databases=databases)
+
+
+def save_config(config: AppConfig, path: Path = Path(CONFIG_FILENAME)) -> None:
+    path = Path(path)
+    path.parent.mkdir(parents=True, exist_ok=True)
+    yaml.safe_dump(
+        config.to_dict(),
+        path.open("w", encoding="utf-8"),
+        allow_unicode=True,
+        sort_keys=False,
+        default_flow_style=False,
+    )
+
+
+__all__ = [
+    "AppConfig",
+    "CloudConfig",
+    "ConfigError",
+    "DatabaseConfig",
+    "ServiceControl",
+    "SQLConfig",
+    "load_config",
+    "save_config",
+]
 
EOF
)