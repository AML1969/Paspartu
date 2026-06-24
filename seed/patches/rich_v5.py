#!/usr/bin/env python3
"""RU v5: final sends always go rich (consistency with rich drafts).

v3 made drafts render rich unconditionally, but finals used a suitability
heuristic (tables/headings/>3500 chars) — short bold-only answers got a
pretty draft and a plain final. Fix: any text final goes rich when the
flag is on (plain text renders identically in rich), except MEDIA
directives, channel-DM topics and >32000 chars.
"""
import py_compile, shutil, sys, time

F = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py"
src = open(F).read()
if "RU v5" in src:
    print("already patched - OK"); sys.exit(0)

OLD = """        has_table = re.search(r"(?m)^\\s*\\|.+\\|\\s*$", content) is not None
        has_heading = re.search(r"(?m)^#{1,6}\\s+\\S", content) is not None
        return has_table or has_heading or len(content) > 3500"""
NEW = """        # RU v5: always rich — drafts render rich unconditionally, so the
        # final must too, otherwise short answers degrade at finalization.
        return True"""
if OLD not in src:
    print("anchor not found - aborting"); sys.exit(1)
bak = F + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(F, bak)
print("backup:", bak)
open(F, "w").write(src.replace(OLD, NEW, 1))
try:
    py_compile.compile(F, doraise=True)
    print("v5 applied, py_compile OK")
except Exception as e:
    shutil.copy2(bak, F)
    print("COMPILE FAILED - reverted:", e); sys.exit(1)
