 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a//dev/null b/backup_tool/configurator.py
index 0000000000000000000000000000000000000000..66ed69e791e5f0a837f3c05928dec6d09a95526f 100644
--- a//dev/null
+++ b/backup_tool/configurator.py
@@ -0,0 +1,324 @@
+"""Interactive helpers for building configuration files."""
+from __future__ import annotations
+
+from dataclasses import dataclass
+from getpass import getpass
+from pathlib import Path
+from typing import Dict, Optional
+
+from .config import CloudConfig, DatabaseConfig, SQLConfig, ServiceControl
+from .secrets import SecretManager
+from .utils import slugify
+
+
+@dataclass
+class InteractiveConfigurator:
+    secret_manager: SecretManager
+
+    def create_database(self, existing_names: Optional[Dict[str, DatabaseConfig]] = None) -> DatabaseConfig:
+        existing_names = existing_names or {}
+        print("Добавление новой базы резервного копирования. Нажмите Ctrl+C для отмены.\n")
+
+        name = self._prompt_unique_name(existing_names)
+        safe_name = slugify(name)
+        db_type = self._prompt_db_type()
+
+        if db_type == "file":
+            source_prompt = "Укажите путь к каталогу или файлу базы 1С (.1CD): "
+            source = self._prompt_non_empty(source_prompt)
+        else:
+            source = self._prompt_optional(
+                "Опционально укажите путь или описание инфобазы (используется в журналах): "
+            )
+
+        backup_dir_default = str(Path("backups") / safe_name)
+        backup_directory = self._prompt_non_empty(
+            f"Каталог для сохранения бэкапов [{backup_dir_default}]: ",
+            default=backup_dir_default,
+        )
+
+        backup_prefix = self._prompt_optional(
+            "Префикс имени файла бэкапа (Enter чтобы использовать название базы): "
+        )
+        retention_days = self._prompt_int(
+            "Сколько дней хранить бэкапы (по умолчанию 7): ",
+            default=7,
+            minimum=0,
+        )
+
+        cloud_config = self._configure_cloud(safe_name)
+
+        if db_type == "sql":
+            sql_config = self._configure_sql(safe_name)
+            service_config = self._configure_service()
+        else:
+            sql_config = SQLConfig()
+            service_config = ServiceControl()
+
+        database = DatabaseConfig(
+            name=name,
+            type=db_type,
+            source=source,
+            backup_directory=backup_directory,
+            backup_prefix=backup_prefix or None,
+            retention_days=retention_days,
+            cloud=cloud_config,
+            service=service_config,
+            sql=sql_config,
+        )
+        database.validate()
+        return database
+
+    # ------------------------------------------------------------------
+    def _configure_cloud(self, safe_name: str) -> CloudConfig:
+        use_cloud = self._prompt_bool("Выгружать бэкапы в облако (Яндекс.Диск)? [y/N]: ", default=False)
+        if not use_cloud:
+            return CloudConfig(enabled=False)
+
+        remote_default = f"/backups/{safe_name}"
+        remote_path = self._prompt_non_empty(
+            f"Папка на Яндекс.Диске для бэкапов [{remote_default}]: ",
+            default=remote_default,
+        )
+        use_token = self._prompt_bool("Использовать OAuth-токен (рекомендуется)? [Y/n]: ", default=True)
+
+        token_secret = login_secret = password_secret = None
+        if use_token:
+            token_secret = self._prompt_secret(
+                prompt="Введите OAuth-токен Яндекс.Диска",
+                suggested_name=f"YANDEX_TOKEN_{safe_name.upper()}",
+            )
+        else:
+            login_secret = self._prompt_secret(
+                prompt="Введите логин для доступа к WebDAV",
+                suggested_name=f"YANDEX_LOGIN_{safe_name.upper()}",
+                update_existing=False,
+            )
+            password_secret = self._prompt_secret(
+                prompt="Введите пароль",
+                suggested_name=f"YANDEX_PASSWORD_{safe_name.upper()}",
+            )
+
+        return CloudConfig(
+            enabled=True,
+            provider="yandex",
+            remote_path=remote_path,
+            token_secret=token_secret,
+            login_secret=login_secret,
+            password_secret=password_secret,
+        )
+
+    # ------------------------------------------------------------------
+    def _configure_sql(self, safe_name: str) -> SQLConfig:
+        print(
+            "\nНастройка клиент-серверной (SQL) базы."
+            "\nКоманда выгрузки должна принимать путь к файлу дампа."
+            " Доступные переменные:"
+            "\n  {sql_dump_file} — полный путь к файлу дампа,"
+            "\n  {sql_dump_dir} — каталог для временных файлов,"
+            "\n  {safe_name}, {name}, {timestamp} и другие значения из контекста."
+        )
+        dump_command = self._prompt_non_empty("Команда выгрузки: ")
+        dump_extension = self._prompt_optional(
+            "Расширение файла дампа (по умолчанию .dt): ",
+            default=".dt",
+        ) or ".dt"
+        working_directory = self._prompt_optional(
+            "Рабочая директория для команды (если нужна): "
+        )
+
+        login_secret = None
+        if self._prompt_bool("Сохранить логин для подключения? [y/N]: ", default=False):
+            login_secret = self._prompt_secret(
+                prompt="Введите логин",
+                suggested_name=f"SQL_LOGIN_{safe_name.upper()}",
+                update_existing=False,
+            )
+
+        password_secret = None
+        if self._prompt_bool("Сохранить пароль для подключения? [y/N]: ", default=False):
+            password_secret = self._prompt_secret(
+                prompt="Введите пароль",
+                suggested_name=f"SQL_PASSWORD_{safe_name.upper()}",
+            )
+
+        env_from_secrets: Dict[str, str] = {}
+        while self._prompt_bool("Добавить переменную окружения из секрета? [y/N]: ", default=False):
+            env_name = self._prompt_non_empty("Имя переменной окружения: ")
+            suggested_name = f"{slugify(env_name, env_name).upper()}_{safe_name.upper()}"
+            secret_name = self._prompt_secret(
+                prompt=f"Введите значение секрета для {env_name}",
+                suggested_name=suggested_name,
+            )
+            env_from_secrets[env_name] = secret_name
+
+        return SQLConfig(
+            dump_command=dump_command,
+            working_directory=working_directory or None,
+            dump_extension=dump_extension,
+            login_secret=login_secret,
+            password_secret=password_secret,
+            env_from_secrets=env_from_secrets,
+        )
+
+    # ------------------------------------------------------------------
+    def _configure_service(self) -> ServiceControl:
+        use_service = self._prompt_bool(
+            "Нужно останавливать службу (ras/Apache) перед бэкапом? [y/N]: ",
+            default=False,
+        )
+        if not use_service:
+            return ServiceControl()
+
+        stop_command = self._prompt_non_empty("Команда остановки службы: ")
+        start_command = self._prompt_non_empty("Команда запуска службы после бэкапа: ")
+        stop_timeout = self._prompt_int(
+            "Таймаут на остановку (секунды, Enter для значения по умолчанию): ",
+            default=None,
+            minimum=0,
+            allow_empty=True,
+        )
+        start_timeout = self._prompt_int(
+            "Таймаут на запуск (секунды, Enter для значения по умолчанию): ",
+            default=None,
+            minimum=0,
+            allow_empty=True,
+        )
+        return ServiceControl(
+            stop_command=stop_command,
+            start_command=start_command,
+            stop_timeout=stop_timeout,
+            start_timeout=start_timeout,
+        )
+
+    # ------------------------------------------------------------------
+    def _prompt_secret(
+        self,
+        *,
+        prompt: str,
+        suggested_name: str,
+        update_existing: bool = True,
+    ) -> str:
+        existing = set(self.secret_manager.list_secrets())
+        while True:
+            name = self._prompt_non_empty(
+                f"Имя секрета [{suggested_name}]: ",
+                default=suggested_name,
+            )
+            if name in existing and not update_existing:
+                if not self._prompt_bool(
+                    f"Секрет '{name}' уже существует. Использовать без изменений? [Y/n]: ",
+                    default=True,
+                ):
+                    continue
+                return name
+
+            if name in existing:
+                if not self._prompt_bool(
+                    f"Секрет '{name}' уже существует. Перезаписать значение? [y/N]: ",
+                    default=False,
+                ):
+                    return name
+
+            value = self._prompt_secret_value(prompt)
+            self.secret_manager.set_secret(name, value)
+            return name
+
+    # ------------------------------------------------------------------
+    def _prompt_secret_value(self, prompt: str) -> str:
+        while True:
+            first = getpass(f"{prompt}: ")
+            second = getpass("Повторите значение: ")
+            if first != second:
+                print("Значения не совпадают, попробуйте ещё раз.")
+                continue
+            if not first:
+                print("Значение не может быть пустым.")
+                continue
+            return first
+
+    # ------------------------------------------------------------------
+    def _prompt_unique_name(self, existing_names: Dict[str, DatabaseConfig]) -> str:
+        while True:
+            name = self._prompt_non_empty("Введите название базы: ")
+            if name.lower() in (key.lower() for key in existing_names):
+                print("База с таким названием уже существует. Выберите другое название.")
+                continue
+            return name
+
+    # ------------------------------------------------------------------
+    def _prompt_db_type(self) -> str:
+        print("Выберите тип базы:")
+        print("  1. Файловая (1CD)")
+        print("  2. Клиент-сервер (SQL)")
+        while True:
+            answer = input("Введите 1 или 2 [1]: ").strip().lower()
+            if not answer or answer in {"1", "ф", "f", "file"}:
+                return "file"
+            if answer in {"2", "s", "sql", "к"}:
+                return "sql"
+            print("Некорректный выбор. Введите 1 или 2.")
+
+    # ------------------------------------------------------------------
+    def _prompt_bool(self, question: str, *, default: bool) -> bool:
+        true_values = {"y", "yes", "д", "да", "true", "1"}
+        false_values = {"n", "no", "н", "нет", "false", "0"}
+        while True:
+            answer = input(question).strip().lower()
+            if not answer:
+                return default
+            if answer in true_values:
+                return True
+            if answer in false_values:
+                return False
+            print("Ответ не распознан. Введите 'y' или 'n'.")
+
+    # ------------------------------------------------------------------
+    def _prompt_non_empty(self, question: str, default: Optional[str] = None) -> str:
+        while True:
+            answer = input(question).strip()
+            if not answer:
+                if default is not None:
+                    return default
+                print("Значение не может быть пустым.")
+                continue
+            return answer
+
+    # ------------------------------------------------------------------
+    def _prompt_optional(
+        self,
+        question: str,
+        default: Optional[str] = None,
+    ) -> Optional[str]:
+        answer = input(question).strip()
+        if not answer:
+            return default
+        return answer
+
+    # ------------------------------------------------------------------
+    def _prompt_int(
+        self,
+        question: str,
+        *,
+        default: Optional[int],
+        minimum: Optional[int] = None,
+        allow_empty: bool = False,
+    ) -> Optional[int]:
+        while True:
+            answer = input(question).strip()
+            if not answer:
+                if allow_empty:
+                    return default
+                if default is not None:
+                    return default
+                print("Значение не может быть пустым.")
+                continue
+            try:
+                value = int(answer)
+            except ValueError:
+                print("Введите целое число.")
+                continue
+            if minimum is not None and value < minimum:
+                print(f"Значение должно быть не меньше {minimum}.")
+                continue
+            return value
 
EOF
)