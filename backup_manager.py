 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a//dev/null b/backup_manager.py
index 0000000000000000000000000000000000000000..5a99f33d0913696d641c51e275ce59bf8fd449e8 100644
--- a//dev/null
+++ b/backup_manager.py
@@ -0,0 +1,202 @@
+"""Command line interface for the 1C backup automation tool."""
+from __future__ import annotations
+
+import argparse
+import logging
+import sys
+from getpass import getpass
+from pathlib import Path
+from typing import Iterable, Optional
+
+from backup_tool.backup import BackupError, BackupRunner
+from backup_tool.cloud import CloudUploadError, CloudUploader
+from backup_tool.config import AppConfig, ConfigError, load_config, save_config
+from backup_tool.configurator import InteractiveConfigurator
+from backup_tool.secrets import SecretError, SecretManager
+
+
+def build_parser() -> argparse.ArgumentParser:
+    parser = argparse.ArgumentParser(
+        description="Утилита для резервного копирования баз 1С и выгрузки на Яндекс.Диск.",
+    )
+    parser.add_argument("--config", default="config.yaml", help="Путь к файлу конфигурации.")
+    parser.add_argument("--key", default="secrets/key.key", help="Путь к файлу ключа шифрования секретов.")
+    parser.add_argument(
+        "--secrets", default="secrets/secrets.json", help="Путь к файлу с зашифрованными секретами."
+    )
+    parser.add_argument("-v", "--verbose", action="count", default=0, help="Увеличить уровень логирования.")
+
+    subparsers = parser.add_subparsers(dest="command")
+
+    subparsers.add_parser("init-key", help="Создать новый ключ шифрования секретов.")
+
+    parser_secret = subparsers.add_parser("set-secret", help="Задать значение секрета.")
+    parser_secret.add_argument("name", help="Имя секрета.")
+    parser_secret.add_argument("--value", help="Значение секрета (если не указано, запрашивается интерактивно).")
+    parser_secret.add_argument(
+        "--stdin",
+        action="store_true",
+        help="Считать значение секрета из STDIN (без запроса подтверждения).",
+    )
+
+    subparsers.add_parser("list-secrets", help="Показать список сохранённых секретов.")
+
+    parser_run = subparsers.add_parser("run", help="Выполнить резервное копирование.")
+    parser_run.add_argument(
+        "-d",
+        "--database",
+        action="append",
+        dest="databases",
+        help="Имя базы из конфигурации (можно указать несколько раз).",
+    )
+
+    subparsers.add_parser("add-base", help="Интерактивно добавить новую базу в конфигурацию.")
+
+    return parser
+
+
+def configure_logging(level: int) -> None:
+    if level >= 2:
+        log_level = logging.DEBUG
+    elif level == 1:
+        log_level = logging.INFO
+    else:
+        log_level = logging.WARNING
+    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
+
+
+def create_secret_manager(args: argparse.Namespace) -> SecretManager:
+    return SecretManager(key_path=Path(args.key), secrets_path=Path(args.secrets))
+
+
+def load_application_config(path: Path) -> AppConfig:
+    try:
+        return load_config(path)
+    except ConfigError as exc:
+        print(f"Ошибка чтения конфигурации: {exc}", file=sys.stderr)
+        sys.exit(1)
+
+
+def handle_init_key(secret_manager: SecretManager) -> None:
+    try:
+        secret_manager.generate_key()
+    except SecretError as exc:
+        print(f"Ошибка: {exc}", file=sys.stderr)
+        sys.exit(1)
+    else:
+        print(f"Создан ключ шифрования: {secret_manager.key_path}")
+
+
+def handle_set_secret(secret_manager: SecretManager, name: str, value: Optional[str], from_stdin: bool) -> None:
+    try:
+        secret_manager.ensure_key_available()
+    except SecretError as exc:
+        print(f"Ошибка: {exc}", file=sys.stderr)
+        sys.exit(1)
+
+    if value is None:
+        if from_stdin:
+            value = sys.stdin.read().rstrip("\n")
+        else:
+            first = getpass("Введите значение секрета: ")
+            second = getpass("Повторите значение: ")
+            if first != second:
+                print("Значения не совпадают.", file=sys.stderr)
+                sys.exit(1)
+            value = first
+
+    try:
+        secret_manager.set_secret(name, value)
+    except SecretError as exc:
+        print(f"Не удалось сохранить секрет: {exc}", file=sys.stderr)
+        sys.exit(1)
+    else:
+        print(f"Секрет '{name}' сохранён в {secret_manager.secrets_path}")
+
+
+def handle_list_secrets(secret_manager: SecretManager) -> None:
+    try:
+        secret_manager.ensure_key_available()
+    except SecretError as exc:
+        print(f"Ошибка: {exc}", file=sys.stderr)
+        sys.exit(1)
+
+    names = list(secret_manager.list_secrets())
+    if not names:
+        print("Секреты отсутствуют.")
+    else:
+        print("Сохранённые секреты:")
+        for name in names:
+            print(f"  - {name}")
+
+
+def handle_run(args: argparse.Namespace, config: AppConfig, secret_manager: SecretManager) -> None:
+    try:
+        secret_manager.ensure_key_available()
+    except SecretError as exc:
+        print(f"Ошибка: {exc}", file=sys.stderr)
+        sys.exit(1)
+    if not config.databases:
+        print("В конфигурации не найдено ни одной базы.", file=sys.stderr)
+        sys.exit(1)
+    cloud_uploader = CloudUploader(secret_manager)
+    runner = BackupRunner(config=config, secret_manager=secret_manager, cloud_uploader=cloud_uploader)
+    try:
+        runner.run_all(args.databases)
+    except (BackupError, CloudUploadError) as exc:
+        print(f"Ошибка выполнения: {exc}", file=sys.stderr)
+        sys.exit(1)
+    else:
+        print("Резервное копирование завершено успешно.")
+
+
+def handle_add_base(args: argparse.Namespace, config: AppConfig, secret_manager: SecretManager, config_path: Path) -> None:
+    try:
+        secret_manager.ensure_key_available()
+    except SecretError as exc:
+        print(f"Ошибка: {exc}", file=sys.stderr)
+        sys.exit(1)
+    configurator = InteractiveConfigurator(secret_manager)
+    existing = {db.name: db for db in config.databases}
+    try:
+        new_db = configurator.create_database(existing)
+    except KeyboardInterrupt:
+        print("\nОперация отменена пользователем.")
+        return
+
+    config.add_database(new_db)
+    save_config(config, config_path)
+    print(f"База '{new_db.name}' добавлена в конфигурацию {config_path}.")
+
+
+def main(argv: Optional[Iterable[str]] = None) -> None:
+    parser = build_parser()
+    args = parser.parse_args(argv)
+    if not args.command:
+        parser.print_help()
+        return
+
+    configure_logging(args.verbose)
+    secret_manager = create_secret_manager(args)
+
+    if args.command == "init-key":
+        handle_init_key(secret_manager)
+        return
+
+    config_path = Path(args.config)
+    config = load_application_config(config_path)
+
+    if args.command == "set-secret":
+        handle_set_secret(secret_manager, args.name, args.value, args.stdin)
+    elif args.command == "list-secrets":
+        handle_list_secrets(secret_manager)
+    elif args.command == "run":
+        handle_run(args, config, secret_manager)
+    elif args.command == "add-base":
+        handle_add_base(args, config, secret_manager, config_path)
+    else:
+        parser.print_help()
+
+
+if __name__ == "__main__":
+    main()
 
EOF
)