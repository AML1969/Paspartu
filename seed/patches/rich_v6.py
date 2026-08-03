#!/usr/bin/env python3
"""RU v6: finalize-edits go rich.

The edit-based streaming transport finalizes by editing the partial message
with format_message (MarkdownV2 + table->bullets) — the last path that
degraded rich content (quarterly-report test: model wrote a perfect
table, user got bullets). Bot API 10.1 editMessageText accepts rich_message:
try a rich edit first (normalized markdown, handles up to 32k without
splitting), fall back to the stock path on any error.
"""
import py_compile, shutil, sys, time

F = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py"
src = open(F).read()
if "RU v6" in src:
    print("already patched - OK"); sys.exit(0)
if "RU v4" not in src:
    print("v4 normalizer missing - aborting"); sys.exit(1)

ANCHOR = """        # Pre-flight: if content already exceeds the limit, split-and-deliver
        # without round-tripping a doomed edit."""
NEW = """        # RU v6: finalize edits go rich when enabled — one rich message up to
        # 32k, no MarkdownV2 degradation, no overflow splitting.
        if finalize and self._rich_enabled() and len(content) <= 32000 \\
                and "MEDIA:" not in content and "[[audio_as_voice]]" not in content:
            try:
                import httpx
                _payload = {
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "rich_message": {"markdown": self._rich_normalize(content)},
                }
                _token = getattr(self.config, "token", None) or getattr(self._bot, "token", "")
                async with httpx.AsyncClient(timeout=30) as _client:
                    _resp = await _client.post(
                        "https://api.telegram.org/bot%s/editMessageText" % _token,
                        json=_payload,
                    )
                _data = _resp.json()
                if _data.get("ok"):
                    logger.info("[Telegram] rich edit finalized (chars=%d, msg_id=%s)",
                                len(content), message_id)
                    return SendResult(success=True, message_id=message_id)
                _desc = str(_data.get("description", ""))
                if "not modified" in _desc.lower():
                    return SendResult(success=True, message_id=message_id)
                logger.warning("[Telegram] rich edit rejected: %s - falling back", _desc[:160])
            except Exception as _e:
                logger.warning("[Telegram] rich edit failed: %s - falling back", _e)

        # Pre-flight: if content already exceeds the limit, split-and-deliver
        # without round-tripping a doomed edit."""
if ANCHOR not in src:
    print("anchor not found - aborting"); sys.exit(1)
bak = F + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(time.time_ns() % 10**9)
shutil.copy2(F, bak)
print("backup:", bak)
open(F, "w").write(src.replace(ANCHOR, NEW, 1))
try:
    py_compile.compile(F, doraise=True)
    print("v6 applied, py_compile OK")
except Exception as e:
    shutil.copy2(bak, F)
    print("COMPILE FAILED - reverted:", e); sys.exit(1)
