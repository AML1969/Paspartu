#!/usr/bin/env python3
"""v2 delta for the RU rich-message patch: allow thread/topic sends and log decisions."""
import py_compile, shutil, sys, time

F = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py"
src = open(F).read()
if "RU rich-message patch" not in src:
    print("base patch missing - aborting"); sys.exit(1)
if "rich v2" in src:
    print("v2 already applied - OK"); sys.exit(0)

OLD_GUARD = """        try:
            if metadata and (metadata.get("direct_messages_topic_id") or self._metadata_thread_id(metadata)):
                return False
        except Exception:
            return False
"""
NEW_GUARD = """        # rich v2: topics/threads are allowed (thread id is passed through);
        # only channel direct-messages topics stay on the legacy path.
        try:
            if metadata and metadata.get("direct_messages_topic_id"):
                return False
        except Exception:
            return False
"""
OLD_PAYLOAD = """            token = getattr(self.config, "token", None) or getattr(self._bot, "token", "")"""
NEW_PAYLOAD = """            try:
                _tid = self._message_thread_id_for_send(self._metadata_thread_id(metadata))
                if _tid:
                    payload["message_thread_id"] = int(_tid)
            except Exception:
                pass
            logger.info("[Telegram] rich path: trying sendRichMessage (chars=%d, thread=%s)",
                        len(content), payload.get("message_thread_id"))
            token = getattr(self.config, "token", None) or getattr(self._bot, "token", "")"""

if OLD_GUARD not in src or OLD_PAYLOAD not in src:
    print("anchors not found - aborting"); sys.exit(1)
bak = F + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(F, bak)
print("backup:", bak)
open(F, "w").write(src.replace(OLD_GUARD, NEW_GUARD, 1).replace(OLD_PAYLOAD, NEW_PAYLOAD, 1))
try:
    py_compile.compile(F, doraise=True)
    print("v2 applied, py_compile OK")
except Exception as e:
    shutil.copy2(bak, F)
    print("COMPILE FAILED - reverted:", e); sys.exit(1)
