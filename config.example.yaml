 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a//dev/null b/config.example.yaml
index 0000000000000000000000000000000000000000..d1d7c0dfd65a6738f5709c6d18510edde72a229f 100644
--- a//dev/null
+++ b/config.example.yaml
@@ -0,0 +1,37 @@
+# Пример конфигурации резервного копирования.
+# Файл можно сгенерировать командой `python backup_manager.py add-base`.
+# Этот пример демонстрирует два типа баз: файловая и клиент-серверная.
+
+databases:
+  - name: "FileBase"
+    type: "file"
+    source: "/path/to/1c/base"
+    backup_directory: "backups/FileBase"
+    backup_prefix: "FileBase"
+    retention_days: 7
+    cloud:
+      enabled: true
+      provider: "yandex"
+      remote_path: "/backups/{safe_name}"
+      token_secret: "YANDEX_TOKEN_FILEBASE"
+  - name: "SQLBase"
+    type: "sql"
+    source: "cluster1/sql_base"
+    backup_directory: "backups/SQLBase"
+    backup_prefix: "SQLBase"
+    retention_days: 7
+    cloud:
+      enabled: false
+    service:
+      stop_command: "systemctl stop srv1cv83"
+      start_command: "systemctl start srv1cv83"
+      stop_timeout: 120
+      start_timeout: 120
+    sql:
+      dump_command: '"/opt/1C/v8.3/x86_64/rac" dump infobase {source} {sql_dump_file} --db-user {sql_login} --db-pwd {sql_password}'
+      dump_extension: ".dt"
+      working_directory: "/opt/1C/v8.3/x86_64"
+      login_secret: "SQL_LOGIN_SQLBASE"
+      password_secret: "SQL_PASSWORD_SQLBASE"
+      env_from_secrets:
+        SRV1C_CLUSTER: "SQL_CLUSTER_SECRET"
 
EOF
)