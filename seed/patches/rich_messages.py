#!/usr/bin/env python3
"""Idempotent patch: opt-in sendRichMessage (Bot API 10.1) for Hermes telegram adapter.

Gated: enabled ONLY if env HERMES_RICH_MESSAGES=1/true OR platforms.telegram
extra.rich_messages=true in the profile config. Without the flag the adapter
behaves exactly as stock (Ruslan's profile unaffected).

Hook: at the top of TelegramAdapter.send() — if enabled and the content
benefits (tables / headings / >3500 chars, <=32000), try raw sendRichMessage
with the agent's raw markdown. On ANY error → fall back to the stock path.
"""
import py_compile, shutil, sys, time

F = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py"
MARKER = "RU rich-message patch"
src = open(F).read()
if MARKER in src:
    print("already patched - OK"); sys.exit(0)

HELPERS = '''    # --- RU rich-message patch: helpers ---
    def _rich_enabled(self):
        import os
        v = os.environ.get("HERMES_RICH_MESSAGES", "").lower()
        if v in ("1", "true", "yes"):
            return True
        if v in ("0", "false", "no"):
            return False
        try:
            return bool(getattr(self.config, "extra", {}).get("rich_messages"))
        except Exception:
            return False

    def _rich_suitable(self, content, reply_to, metadata):
        import re
        if not content or len(content) > 32000:
            return False
        if "MEDIA:" in content or "[[audio_as_voice]]" in content:
            return False
        try:
            if metadata and (metadata.get("direct_messages_topic_id") or self._metadata_thread_id(metadata)):
                return False
        except Exception:
            return False
        has_table = re.search(r"(?m)^\\s*\\|.+\\|\\s*$", content) is not None
        has_heading = re.search(r"(?m)^#{1,6}\\s+\\S", content) is not None
        return has_table or has_heading or len(content) > 3500

    async def _send_rich(self, chat_id, content, reply_to, metadata):
        """Try sendRichMessage; return SendResult on success, None to fall back."""
        try:
            import httpx
            payload = {"chat_id": int(chat_id), "rich_message": {"markdown": content}}
            if reply_to:
                try:
                    payload["reply_parameters"] = {
                        "message_id": int(reply_to),
                        "allow_sending_without_reply": True,
                    }
                except (TypeError, ValueError):
                    pass
            token = getattr(self.config, "token", None) or getattr(self._bot, "token", "")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.telegram.org/bot%s/sendRichMessage" % token,
                    json=payload,
                )
            data = resp.json()
            if data.get("ok"):
                mid = data["result"]["message_id"]
                logger.info("[Telegram] rich message sent (chars=%d, msg_id=%s)", len(content), mid)
                return SendResult(success=True, message_id=mid,
                                  raw_response={"message_ids": [mid], "rich": True})
            logger.warning("[Telegram] sendRichMessage rejected: %s - falling back",
                           str(data.get("description"))[:200])
            return None
        except Exception as e:
            logger.warning("[Telegram] sendRichMessage failed: %s - falling back", e)
            return None
    # --- end RU rich-message patch helpers ---

'''

ANCHOR_DEF = "    async def send(\n"
if src.count(ANCHOR_DEF) != 1:
    print("ANCHOR_DEF not unique - aborting"); sys.exit(1)

GUARD = """        # Skip whitespace-only text to prevent Telegram 400 empty-text errors.
        if not content or not content.strip():
            return SendResult(success=True, message_id=None)
"""
HOOK = GUARD + """
        # --- RU rich-message patch: opt-in native rich sending ---
        if self._rich_enabled() and self._rich_suitable(content, reply_to, metadata):
            _rich_res = await self._send_rich(chat_id, content, reply_to, metadata)
            if _rich_res is not None:
                return _rich_res
        # --- end RU rich-message patch ---
"""
if GUARD not in src:
    print("GUARD anchor not found - aborting"); sys.exit(1)

bak = F + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(F, bak)
print("backup:", bak)
out = src.replace(ANCHOR_DEF, HELPERS + ANCHOR_DEF, 1).replace(GUARD, HOOK, 1)
open(F, "w").write(out)
try:
    py_compile.compile(F, doraise=True)
    print("telegram.py py_compile OK - rich patch applied")
except Exception as e:
    shutil.copy2(bak, F)
    print("COMPILE FAILED - reverted:", e); sys.exit(1)
