# Учит web_extract-инструмент про бескключевой провайдер 'direct' (web_direct).
# Без него _is_backend_available('direct')=False -> extract молча падает в ddgs
# (search-only) -> 'cannot extract URL content'. Идемпотентен, с бэкапом и py_compile.
import io,sys,py_compile,shutil,time,glob
_hits=glob.glob("/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/tools/web_tools.py")
if not _hits: print("FAIL: web_tools.py не найден"); sys.exit(2)
F=_hits[0]
s=io.open(F,encoding="utf-8").read()
if 'backend == "direct"' in s:
    print("web_tools.py already has direct branch — skip"); sys.exit(0)
anchor='            return False\n    return False\n\n\ndef _ddgs_package_importable'
if anchor not in s:
    print("ANCHOR MISSING (abort)"); sys.exit(2)
ins=('            return False\n'
     '    if backend == "direct":\n'
     '        try:\n'
     '            import httpx  # noqa: F401\n'
     '            return True\n'
     '        except ImportError:\n'
     '            return False\n'
     '    return False\n\n\ndef _ddgs_package_importable')
bak=F+".bak-"+time.strftime("%Y%m%d-%H%M%S"); shutil.copy2(F,bak); print("backup:",bak)
io.open(F,"w",encoding="utf-8").write(s.replace(anchor,ins,1))
try:
    py_compile.compile(F,doraise=True); print("web_tools.py py_compile OK — direct extract enabled")
except Exception as e:
    shutil.copy2(bak,F); print("compile FAILED, reverted:",e); sys.exit(3)
