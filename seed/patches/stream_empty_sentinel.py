#!/usr/bin/env python3
"""Idempotent patch: stream consumer must not deliver the "(empty)" sentinel.

DeepSeek sometimes returns no text after tool calls; the agent substitutes
the internal sentinel "(empty)". gateway/run.py converts it to a friendly
warning on the normal send path, but the streaming path (stream_consumer)
delivers it verbatim to the chat, causing a literal "(empty)" message plus
a duplicate warning. Fix: treat the sentinel as empty in _clean_for_display
(single funnel for all stream display paths).
"""
import py_compile, shutil, sys, time

F = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/stream_consumer.py"
MARKER = "RU empty-sentinel guard"
src = open(F).read()
if MARKER in src:
    print("already patched — OK")
    sys.exit(0)
ANCHOR = "        if \"MEDIA:\" not in text and \"[[audio_as_voice]]\" not in text:"
GUARD = ("        if text.strip() == \"(empty)\":  # " + MARKER + "\n"
         "            return \"\"\n")
if ANCHOR not in src:
    print("ANCHOR not found — aborting, no changes"); sys.exit(1)
bak = F + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(F, bak)
print("backup:", bak)
open(F, "w").write(src.replace(ANCHOR, GUARD + ANCHOR, 1))
try:
    py_compile.compile(F, doraise=True)
    print("stream_consumer.py py_compile OK — patched")
except Exception as e:
    shutil.copy2(bak, F)
    print("COMPILE FAILED — reverted:", e); sys.exit(1)
