#!/usr/bin/env python3
"""RU v4: markdown normalizer for rich sends.

Models emit imperfect markdown: unbalanced ** poisons Telegram's delimiter
pairing (some bold renders, some shows literal **), and --- without blank
lines around stays literal. Normalize before sendRichMessage /
sendRichMessageDraft: keep well-formed same-line **pairs**, strip stray **,
pad --- with blank lines. Fenced code blocks are passed through untouched.
"""
import py_compile, shutil, sys, time

F = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py"
src = open(F).read()
if "RU v4" in src:
    print("already patched - OK"); sys.exit(0)
if "RU rich-message patch: helpers" not in src or "RU v3" not in src:
    print("base patches missing - aborting"); sys.exit(1)

HELPER = '''    # --- RU v4: markdown normalizer for rich sends ---
    @staticmethod
    def _rich_normalize(md):
        import re
        out = []
        in_fence = False
        for line in md.split("\\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                out.append(line)
                continue
            if in_fence:
                out.append(line)
                continue
            if stripped in ("---", "***", "___"):
                if out and out[-1].strip():
                    out.append("")
                out.append("---")
                out.append("")
                continue
            protected = []
            def _keep(m):
                protected.append(m.group(0))
                return "\\x00%d\\x00" % (len(protected) - 1)
            tmp = re.sub(r"\\*\\*([^*\\n]+?)\\*\\*", _keep, line)
            tmp = tmp.replace("**", "")
            for i, p in enumerate(protected):
                tmp = tmp.replace("\\x00%d\\x00" % i, p)
            out.append(tmp)
        return "\\n".join(out)

'''
ANCHOR_H = "    # --- RU rich-message patch: helpers ---\n"
A_SEND = '            payload = {"chat_id": int(chat_id), "rich_message": {"markdown": content}}'
N_SEND = '            content = self._rich_normalize(content)  # RU v4\n            payload = {"chat_id": int(chat_id), "rich_message": {"markdown": content}}'
A_DRAFT = '                    "rich_message": {"markdown": text},'
N_DRAFT = '                    "rich_message": {"markdown": self._rich_normalize(text)},  # RU v4'
for name, a in (("H", ANCHOR_H), ("SEND", A_SEND), ("DRAFT", A_DRAFT)):
    if a not in src:
        print("anchor %s not found - aborting" % name); sys.exit(1)

bak = F + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(time.time_ns() % 10**9)
shutil.copy2(F, bak)
print("backup:", bak)
out = src.replace(ANCHOR_H, ANCHOR_H + HELPER, 1).replace(A_SEND, N_SEND, 1).replace(A_DRAFT, N_DRAFT, 1)
open(F, "w").write(out)
try:
    py_compile.compile(F, doraise=True)
    print("v4 applied, py_compile OK")
except Exception as e:
    shutil.copy2(bak, F)
    print("COMPILE FAILED - reverted:", e); sys.exit(1)
