#!/bin/bash
# BIF (bif:1.2): инициализация Codex в контейнере. Идемпотентно, каждый старт.
# /root/.codex эфемерен (вне тома) → конфиги генерятся из env при каждом старте.
# Скрипты в /data/codex-jobs ПЕРЕЗАПИСЫВАЮТСЯ из образа (обновляются с ним),
# рабочие данные задач (номера, логи) не трогаются.
set -eu
SEEDC=/opt/hermes-seed/codex
mkdir -p /data/codex /data/codex-jobs /data/codex-work /root/.codex
[ -f /data/codex/enabled ] || echo on > /data/codex/enabled
install -m 755 "$SEEDC/codex_task.sh"       /data/codex-jobs/codex_task.sh
install -m 755 "$SEEDC/codex_task_runner.sh" /data/codex-jobs/codex_task_runner.sh
install -m 755 "$SEEDC/codex_watch_tick.sh"  /data/codex-jobs/codex_watch_tick.sh
cp -f "$SEEDC/models.json"      /root/.codex/models.json
cp -f "$SEEDC/sol.config.toml"  /root/.codex/sol.config.toml
: "${DEEPSEEK_API_KEY:?codex_setup: нет DEEPSEEK_API_KEY в env}"
cat > /root/.codex/config.toml <<EOF
model = "deepseek-v4-pro"
model_reasoning_effort = "high"
approval_policy = "never"
model_provider = "deepseek"
preferred_auth_method = "apikey"
forced_login_method = "api"
model_catalog_json = "/root/.codex/models.json"

[sandbox_workspace_write]
network_access = true

[projects."/data/codex-work"]
trust_level = "trusted"

[projects."/tmp"]
trust_level = "trusted"

[model_providers.deepseek]
name = "deepseek"
base_url = "https://api.deepseek.com/"
wire_api = "responses"
experimental_bearer_token = "$DEEPSEEK_API_KEY"
EOF
chmod 600 /root/.codex/config.toml
if [ -n "${OPENAI_API_KEY:-}" ]; then
  printf '{\n  "OPENAI_API_KEY": "%s"\n}\n' "$OPENAI_API_KEY" > /root/.codex/auth.json
  chmod 600 /root/.codex/auth.json
else
  echo "[codex_setup] ⚠️ OPENAI_API_KEY пуст — запасной путь sol работать не будет"
fi
echo "[codex_setup] ok (enabled=$(cat /data/codex/enabled))"
