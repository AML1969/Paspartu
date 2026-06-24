import io,sys,py_compile,shutil,time,glob
F=glob.glob("/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/gateway/platforms/telegram.py")[0]
s=io.open(F,encoding="utf-8").read()
if "Требуется подтверждение команды" in s:
    print("telegram.py already localized — skip"); sys.exit(0)
reps=[
 ("⚠️ <b>Command Approval Required</b>", "⚠️ <b>Требуется подтверждение команды</b>"),
 ("f\"Reason: {_html.escape(description)}\"", "f\"Причина: {_html.escape(description)}\""),
 ("\"✅ Allow Once\"", "\"✅ Разрешить один раз\""),
 ("\"✅ Session\"", "\"✅ На сессию\""),
 ("\"✅ Always\"", "\"✅ Всегда\""),
 ("\"❌ Deny\"", "\"❌ Отменить\""),
 ("\"✅ Approve Once\"", "\"✅ Разрешить один раз\""),
 ("\"🔒 Always Approve\"", "\"🔒 Всегда разрешать\""),
 ("\"❌ Cancel\"", "\"❌ Отменить\""),
]
miss=[o for o,_ in reps if o not in s]
if miss:
    print("MISSING (abort):"); [print("  ",repr(m)) for m in miss]; sys.exit(2)
bak=F+".bak-"+time.strftime("%Y%m%d-%H%M%S"); shutil.copy2(F,bak); print("backup:",bak)
for o,n in reps: s=s.replace(o,n)
io.open(F,"w",encoding="utf-8").write(s)
try:
    py_compile.compile(F,doraise=True); print("telegram.py py_compile OK — localized")
except Exception as e:
    shutil.copy2(bak,F); print("compile FAILED, reverted:",e); sys.exit(3)
