#!/usr/bin/env python3
"""RU v3: rich draft streaming + full-text final.

1. telegram.py send_draft(): when rich is enabled, stream the raw markdown
   via raw sendRichMessageDraft (live rich rendering); fallback to stock
   MarkdownV2 draft on any error.
2. stream_consumer.py: draft frames no longer poison _last_sent_text
   (ephemeral previews are not "shown" text), so the fallback-final path
   always delivers the FULL final text via adapter.send (which handles
   rich / formatting / splitting internally).
"""
import py_compile, shutil, sys, time

SP = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages"
TG = SP + "/gateway/platforms/telegram.py"
SC = SP + "/gateway/stream_consumer.py"
MARKER = "RU v3"
ts = time.strftime("%Y%m%d-%H%M%S") + "-" + str(time.time_ns() % 10**9)

tg = open(TG).read()
sc = open(SC).read()
if MARKER in tg and MARKER in sc:
    print("already patched - OK"); sys.exit(0)

# ---------- telegram.py ----------
A1 = "        thread_id = self._metadata_thread_id(metadata)\n\n        # Apply the same MarkdownV2 conversion"
N1 = """        thread_id = self._metadata_thread_id(metadata)

        # RU v3: rich draft streaming — render markdown live in the preview.
        if self._rich_enabled():
            try:
                import httpx
                _payload = {
                    "chat_id": int(chat_id),
                    "draft_id": int(draft_id),
                    "rich_message": {"markdown": text},
                }
                if thread_id is not None:
                    _payload["message_thread_id"] = int(thread_id)
                _token = getattr(self.config, "token", None) or getattr(self._bot, "token", "")
                async with httpx.AsyncClient(timeout=15) as _client:
                    _resp = await _client.post(
                        "https://api.telegram.org/bot%s/sendRichMessageDraft" % _token,
                        json=_payload,
                    )
                if _resp.json().get("ok"):
                    return SendResult(success=True, message_id=None)
                logger.debug("[%s] sendRichMessageDraft rejected, falling back to MarkdownV2 draft", self.name)
            except Exception as _e:
                logger.debug("[%s] sendRichMessageDraft failed (%s), falling back", self.name, _e)

        # Apply the same MarkdownV2 conversion"""
if A1 not in tg:
    print("TG anchor A1 not found - aborting"); sys.exit(1)

# ---------- stream_consumer.py ----------
A2 = """        # Frame delivered.  Track text for parity with edit-based no-op skip.
        self._last_sent_text = text
        return True"""
N2 = """        # RU v3: drafts are ephemeral — track them separately so they never
        # poison the visible-prefix continuation math.
        self._last_draft_text = text
        return True"""
A3 = "            if text == self._last_sent_text:\n                return True\n            ok = await self._send_draft_frame(text)"
N3 = "            if text == getattr(self, \"_last_draft_text\", None):  # RU v3\n                return True\n            ok = await self._send_draft_frame(text)"
A4 = """        final_text = self._clean_for_display(text)
        continuation = self._continuation_text(final_text)
        self._fallback_final_send = False"""
N4 = """        final_text = self._clean_for_display(text)
        # RU v3: if we streamed via ephemeral drafts (no real partial message
        # exists), the user keeps nothing on screen — the final must be the
        # FULL text, never a continuation.
        if getattr(self, "_last_draft_text", None) and self._message_id is None:
            self._fallback_prefix = ""
            self._last_sent_text = ""
        continuation = self._continuation_text(final_text)
        self._fallback_final_send = False"""
for name, anchor, blob in (("A2", A2, sc), ("A3", A3, sc), ("A4", A4, sc)):
    if anchor not in blob:
        print("SC anchor %s not found - aborting" % name); sys.exit(1)

shutil.copy2(TG, TG + ".bak-" + ts)
shutil.copy2(SC, SC + ".bak-" + ts)
print("backups: *.bak-" + ts)
open(TG, "w").write(tg.replace(A1, N1, 1))
open(SC, "w").write(sc.replace(A2, N2, 1).replace(A3, N3, 1).replace(A4, N4, 1))
try:
    py_compile.compile(TG, doraise=True)
    py_compile.compile(SC, doraise=True)
    print("v3 applied, both files py_compile OK")
except Exception as e:
    shutil.copy2(TG + ".bak-" + ts, TG)
    shutil.copy2(SC + ".bak-" + ts, SC)
    print("COMPILE FAILED - both reverted:", e); sys.exit(1)
