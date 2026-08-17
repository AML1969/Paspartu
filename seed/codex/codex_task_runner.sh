#!/usr/bin/env bash
# BIF: внутренний фоновый исполнитель одной Codex-задачи (bif:1.2). Не вызывать напрямую.
JD="$1"
TASK="$(cat "$JD/task.txt")"
WORKDIR="$(cat "$JD/workdir.txt" 2>/dev/null || echo /data/codex-work)"
LLM="$(cat "$JD/llm.txt" 2>/dev/null || echo deepseek)"
PARGS=""
[ "$LLM" = "sol" ] && PARGS="--profile sol"
# codex-container-sandbox-fix: в контейнере bwrap не может создать user
# namespace (Docker seccomp) → падают ВСЕ файловые операции Codex.
# Изоляцией служит сам контейнер, поэтому внутреннюю песочницу отключаем.
if [ -f /.dockerenv ]; then SBX="danger-full-access"; else SBX="workspace-write"; fi
codex exec $PARGS --skip-git-repo-check -s "$SBX" -C "$WORKDIR" \
  --output-last-message "$JD/answer.txt" "$TASK" > "$JD/run.log" 2>&1
echo $? > "$JD/exit_code"
