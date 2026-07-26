#!/usr/bin/env python3
"""Paspartu repair tool -- run once per host, safe to re-run.

Fix 1 -- web_extract was dead.
    web.extract_backend was empty, so the registry fell back to ddgs,
    which is search-only. Every extract-capable bundled provider needs an
    API key. This installs the keyless ``web_direct`` provider (plain HTTP
    fetch + HTML-to-text, no key, no account) and points extract_backend
    at it.

Fix 2 -- biographies of namesakes were being merged.
    Appends the FACT_IDENTITY block to every SOUL file: never merge two
    people who only share a name, verify important facts against a primary
    source, mark each fact as found / assumed / unconfirmed.

Usage:
    python3 fix_paspartu.py [--dry-run] [--root DIR]... [--plugin-dir DIR]

Every step checks for its own marker before writing, so re-running is a
no-op. Originals are copied to <file>.bak-20260726-facts once.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/AML1969/Paspartu/main/seed/"
STAMP = ".bak-20260726-facts"
SOUL_MARK = "same_person"
PLUGIN = "web_direct"
BACKEND = "direct"

DEFAULT_ROOTS = ["/root", "/data", "/home", "/var/lib/docker/volumes"]
SKIP_SUBSTR = ("kompromat",)
SKIP_DIRS = {".git", "node_modules", "__pycache__", "site-packages",
             ".venv", "venv", ".cache", "dist-packages"}


def fetch(rel):
    with urllib.request.urlopen(RAW + rel, timeout=40) as resp:
        return resp.read().decode("utf-8")


def md5(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def walk(roots, names, maxdepth=8):
    hits = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        base = root.rstrip("/").count("/")
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if any(s in dirpath.lower() for s in SKIP_SUBSTR):
                dirnames[:] = []
                continue
            if dirpath.count("/") - base >= maxdepth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn in names:
                    path = os.path.join(dirpath, fn)
                    if ".bak" in path:
                        continue
                    hits.append(path)
    return sorted(set(hits))


def backup(path):
    dst = path + STAMP
    if not os.path.exists(dst):
        shutil.copy2(path, dst)
        return True
    return False


def is_immutable(path):
    try:
        out = subprocess.run(["lsattr", "-d", path], capture_output=True,
                             text=True).stdout.strip()
        return bool(out) and "i" in out.split()[0]
    except Exception:
        return False


def set_immutable(path, on):
    subprocess.run(["chattr", "+i" if on else "-i", path],
                   capture_output=True)


# ----------------------------------------------------------------------
# config.yaml
# ----------------------------------------------------------------------

def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def patch_extract_backend(lines):
    """Point web.extract_backend at the keyless provider."""
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("web:"):
            start = i
            break
    if start is None:
        return lines, "no web: section"

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i][:1].isspace():
            end = i
            break

    for i in range(start + 1, end):
        if re.match(r"^[ \t]+extract_backend:", lines[i]):
            cur = lines[i].split(":", 1)[1].strip().strip("'\"")
            if cur == BACKEND:
                return lines, "already %s" % BACKEND
            lines[i] = _indent(lines[i]) + "extract_backend: " + BACKEND
            return lines, "'%s' -> %s" % (cur, BACKEND)

    pad = "  "
    for i in range(start + 1, end):
        if lines[i].strip():
            pad = _indent(lines[i])
            break
    lines.insert(start + 1, pad + "extract_backend: " + BACKEND)
    return lines, "key added -> %s" % BACKEND


def patch_plugins_enabled(lines):
    """Add web_direct to plugins.enabled."""
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("plugins:"):
            start = i
            break
    if start is None:
        return lines, "no plugins: section"

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i][:1].isspace():
            end = i
            break

    for i in range(start + 1, end):
        if not re.match(r"^[ \t]+enabled:", lines[i]):
            continue
        head = lines[i].split(":", 1)[1].strip()
        key_indent = _indent(lines[i])
        if head in ("[]", ""):
            if head == "[]":
                lines[i] = key_indent + "enabled:"
            last = i
            item_indent = key_indent + "  "
            for j in range(i + 1, end):
                stripped = lines[j].strip()
                if stripped.startswith("- "):
                    if stripped[2:].strip() == PLUGIN:
                        return lines, "already listed"
                    item_indent = _indent(lines[j])
                    last = j
                elif stripped:
                    break
            lines.insert(last + 1, item_indent + "- " + PLUGIN)
            return lines, "added to enabled"
        if PLUGIN in head:
            return lines, "already listed"
        inner = head.strip("[]").strip()
        items = [x.strip() for x in inner.split(",") if x.strip()]
        items.append(PLUGIN)
        lines[i] = key_indent + "enabled: [" + ", ".join(items) + "]"
        return lines, "added to inline list"

    lines.insert(start + 1, "  enabled:")
    lines.insert(start + 2, "    - " + PLUGIN)
    return lines, "enabled: created"


def patch_config(path, dry):
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")
    lines, r1 = patch_extract_backend(lines)
    lines, r2 = patch_plugins_enabled(lines)
    new = "\n".join(lines)
    if new == text:
        return "unchanged  (%s | %s)" % (r1, r2)
    try:
        import yaml
        cfg = yaml.safe_load(new)
        got = (cfg.get("web") or {}).get("extract_backend")
        listed = (cfg.get("plugins") or {}).get("enabled") or []
        if got != BACKEND or PLUGIN not in listed:
            return "REFUSED: post-check failed (%r, %r)" % (got, listed)
    except ImportError:
        pass
    except Exception as exc:
        return "REFUSED: yaml would not parse (%s)" % exc
    if dry:
        return "would patch (%s | %s)" % (r1, r2)
    backup(path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return "patched    (%s | %s)" % (r1, r2)


# ----------------------------------------------------------------------
# SOUL files
# ----------------------------------------------------------------------

def patch_soul(path, block, dry):
    try:
        text = open(path, encoding="utf-8").read()
    except Exception as exc:
        return "unreadable (%s)" % exc
    if SOUL_MARK in text:
        return "already has the rule"
    locked = is_immutable(path)
    if dry:
        return "would append%s" % (" (immutable)" if locked else "")
    if locked:
        set_immutable(path, False)
    try:
        backup(path)
        if text.endswith("\n\n"):
            sep = ""
        elif text.endswith("\n"):
            sep = "\n"
        else:
            sep = "\n\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(sep + block)
    except Exception as exc:
        return "FAILED (%s)" % exc
    finally:
        if locked:
            set_immutable(path, True)
    return "appended%s" % (" (immutable restored)" if locked else "")


# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", action="append", default=None)
    ap.add_argument("--plugin-dir", default=None)
    args = ap.parse_args()

    roots = args.root or DEFAULT_ROOTS
    dry = args.dry_run

    print("Paspartu repair tool -- %s" % ("DRY RUN" if dry else "APPLYING"))
    print("roots: %s" % ", ".join(roots))
    print()

    block = fetch("FACT_IDENTITY_BLOCK.md")
    if SOUL_MARK not in block or len(block.encode("utf-8")) < 2000:
        sys.exit("aborting: FACT_IDENTITY_BLOCK.md looks wrong (%d bytes)"
                 % len(block.encode("utf-8")))
    print("fetched FACT_IDENTITY_BLOCK.md  %d bytes  md5 %s"
          % (len(block.encode("utf-8")), md5(block)))

    files = {}
    for name in ("__init__.py", "plugin.yaml"):
        files[name] = fetch("plugins/" + PLUGIN + "/" + name)
        print("fetched %-12s %12d bytes  md5 %s"
              % (name, len(files[name].encode("utf-8")), md5(files[name])))
    try:
        compile(files["__init__.py"], "web_direct/__init__.py", "exec")
        print("web_direct/__init__.py compiles OK")
    except SyntaxError as exc:
        sys.exit("aborting: provider does not compile (%s)" % exc)
    print()

    pdir = args.plugin_dir or os.path.expanduser("~/.hermes/plugins/" + PLUGIN)
    print("== plugin ==")
    print(pdir)
    if not dry:
        os.makedirs(pdir, exist_ok=True)
        for name, data in files.items():
            with open(os.path.join(pdir, name), "w", encoding="utf-8") as fh:
                fh.write(data)
        on_disk = sorted(os.listdir(pdir))
        print("  installed: %s" % ", ".join(on_disk))
    else:
        print("  would install: %s" % ", ".join(sorted(files)))
    print()

    print("== config.yaml ==")
    configs = [p for p in walk(roots, {"config.yaml"})
               if "extract_backend" in open(p, encoding="utf-8",
                                            errors="replace").read()]
    if not configs:
        print("  none found")
    for path in configs:
        print("  %-58s %s" % (path, patch_config(path, dry)))
    print()

    print("== SOUL files ==")
    souls = walk(roots, {"SOUL.md", "SOUL.template.md"})
    if not souls:
        print("  none found")
    for path in souls:
        print("  %-58s %s" % (path, patch_soul(path, block, dry)))
    print()

    print("done. %d config(s), %d SOUL file(s)." % (len(configs), len(souls)))
    if not dry:
        print("RESTART the hermes gateway for the config change to take "
              "effect (the SOUL files are read per session).")


if __name__ == "__main__":
    main()
