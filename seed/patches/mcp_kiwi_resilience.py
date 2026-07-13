#!/usr/bin/env python3
"""Idempotent patch: MCP reconnect resilience (Kiwi / stateless HTTP).

Symptom: the `kiwi` MCP server (https://mcp.kiwi.com) silently disappears
after a day or three — `hermes mcp test kiwi` fails, the flight-search tool
is gone from the model's toolset, and only a full Hermes restart brings it
back.

Three independent defects combine into that permanent death:

A. RETRY BUDGET NEVER RESETS (the fatal one).
   `retries` / `backoff` are initialized ONCE before `while True` in
   `_run()` and are never reset after a *successful* reconnect. So the five
   lives granted by `_MAX_RECONNECT_RETRIES` are spent cumulatively over the
   entire process lifetime: five unrelated blips spread over three days
   exhaust the budget, `_run()` returns, `self.session` stays None and
   `register_mcp_servers()` skips the server forever.
   Fix: remember when the session was established; if it lived >= 60s before
   dying, the endpoint is fundamentally healthy — reset `retries` to 0 and
   `backoff` to 1.0. Five attempts then mean "Kiwi is really down right now",
   not "Kiwi hiccuped five times this week".

B. KEEPALIVE IS A NET NEGATIVE ON STATELESS HTTP.
   `_wait_for_lifecycle_event()` fires `session.list_tools()` every 180s to
   keep TCP warm. Kiwi is a *stateless* Streamable-HTTP server: it never
   issues an `Mcp-Session-Id` and holds no connection state, so there is
   nothing to keep alive — but every keepalive POST is another chance to
   catch one of Kiwi's occasional 503s, and a 503 inside the POST tears the
   anyio task group down and kills the session. The keepalive is therefore
   the *only* source of idle-time disconnects for this server.
   Fix (two layers, both gated so stdio servers such as `hotels` are
   untouched):
     - soft keepalive: one failed `list_tools()` no longer kills the
       session; retry once after 10s and only reconnect if the retry fails.
     - stateless HTTP (no Mcp-Session-Id after initialize) gets a 900s
       interval instead of 180s — 5x fewer chances to trip over a 503.

C. THE REAL ERROR WAS INVISIBLE.
   Failures are logged with a bare `%s` on an ExceptionGroup, whose str() is
   the useless "unhandled errors in a TaskGroup (1 sub-exception)". The 503
   underneath was never printed. Fix: `_kiwi_exc_detail()` recurses into
   `.exceptions`, plus `exc_info=True` on the reconnect-path warnings.

NB: MCPServerTask declares __slots__ — the two new attributes are added
there as well, otherwise assigning them raises AttributeError and every MCP
server (including stdio ones) fails to connect.

Touches only tools/mcp_tool.py. Behaviour for stdio transports is unchanged
(the interval bump is gated on `_is_http()` + a null session id; the soft
keepalive retry applies to all transports but is strictly more forgiving).
"""
import os
import py_compile
import re
import shutil
import sys
import time

# Target file. Default = pipx layout of the live host (188.166.122.243).
# The literal below is kept on ONE line on purpose: the BIF Dockerfile
# sed-retargets the pipx prefix to the container's site-packages, and sed is
# line-based — a string split across two lines would silently not match.
# Override for other layouts (e.g. inside the bif container, where hermes is
# installed with plain pip):
#     MCP_TOOL_PATH=/usr/local/lib/python3.12/site-packages/tools/mcp_tool.py python3 mcp_kiwi_resilience.py
#     python3 mcp_kiwi_resilience.py /path/to/tools/mcp_tool.py
# If neither is given and the default path does not exist, fall back to
# importlib discovery of `tools.mcp_tool`.
F = "/root/.local/share/pipx/venvs/hermes-agent/lib/python3.12/site-packages/tools/mcp_tool.py"
_cli = sys.argv[1] if len(sys.argv) > 1 else ""
F = os.environ.get("MCP_TOOL_PATH") or _cli or F
if not os.path.exists(F):
    try:
        import importlib.util
        _spec = importlib.util.find_spec("tools.mcp_tool")
        if _spec and _spec.origin and os.path.exists(_spec.origin):
            F = _spec.origin
    except Exception:
        pass
if not os.path.exists(F):
    print("target not found: %s (set MCP_TOOL_PATH or pass the path as argv[1])" % F)
    sys.exit(1)
print("target:", F)

MARKER = "KIWI-RESILIENCE"

src = open(F).read()
if MARKER in src:
    print("already patched — OK")
    sys.exit(0)

orig = src

# --------------------------------------------------------------------------
# 0. Module-level helpers + constants (after the existing Constants block).
# --------------------------------------------------------------------------
A0_OLD = "_MAX_BACKOFF_SECONDS = 60\n"
A0_NEW = '''_MAX_BACKOFF_SECONDS = 60

# --- KIWI-RESILIENCE ------------------------------------------------------
import time as _kiwi_time  # noqa: E402

# A session that stayed up this long proves the endpoint is fundamentally
# healthy; a failure after that is a fresh incident and gets a fresh budget.
_SESSION_HEALTHY_SECONDS = 60.0

# Stateless Streamable-HTTP servers (no Mcp-Session-Id => no connection state
# to keep warm) get a long keepalive interval: each keepalive POST is a chance
# to catch a transient 5xx that would tear down the anyio task group, and it
# buys nothing in return. stdio servers are never affected by this.
_STATELESS_KEEPALIVE_INTERVAL = 900


def _kiwi_exc_detail(exc, _depth=0):
    """Render an exception, unpacking ExceptionGroup / TaskGroup wrappers.

    anyio surfaces transport failures as an ExceptionGroup whose str() is just
    "unhandled errors in a TaskGroup (1 sub-exception)" — the actual HTTP 503
    from the server is buried in ``.exceptions``. Logging that with a bare %s
    tells you nothing, so recurse into the group.
    """
    try:
        subs = getattr(exc, "exceptions", None)
        if subs and _depth < 5:
            inner = "; ".join(_kiwi_exc_detail(e, _depth + 1) for e in subs)
            return "%s(%s)" % (type(exc).__name__, inner)
        return "%s: %s" % (type(exc).__name__, exc)
    except Exception:  # never let diagnostics break the reconnect loop
        return repr(exc)
# --- /KIWI-RESILIENCE -----------------------------------------------------
'''

# --------------------------------------------------------------------------
# 1a. __slots__: MCPServerTask defines __slots__, so a new attribute MUST be
#     declared there or every assignment raises AttributeError at runtime
#     (which the reconnect loop swallows as "Connection failed").
# --------------------------------------------------------------------------
A1S_OLD = """        "_rpc_lock", "_pending_refresh_tasks",
        "initialize_result",
    )
"""
A1S_NEW = """        "_rpc_lock", "_pending_refresh_tasks",
        "initialize_result",
        # KIWI-RESILIENCE: __slots__ is authoritative on this class — an
        # undeclared attribute cannot be assigned at all.
        "_session_started_at", "_mcp_session_id_getter",
    )
"""

# --------------------------------------------------------------------------
# 1b. __init__: declare the new attributes so they always exist.
# --------------------------------------------------------------------------
A1_OLD = "        self.initialize_result: Optional[Any] = None\n"
A1_NEW = '''        self.initialize_result: Optional[Any] = None
        # KIWI-RESILIENCE: monotonic timestamp of the current session's
        # establishment (None while disconnected) + the Streamable-HTTP
        # session-id getter, used to detect stateless servers.
        self._session_started_at: Optional[float] = None
        self._mcp_session_id_getter = None
'''

# --------------------------------------------------------------------------
# 2. Capture the session-id getter in BOTH HTTP branches (new + legacy API).
#    The two blocks differ only in indentation, which makes each unique.
# --------------------------------------------------------------------------
A2_OLD = """                    async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                        self.initialize_result = await session.initialize()
                        self.session = session
                        await self._discover_tools()
"""
A2_NEW = """                    async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                        self.initialize_result = await session.initialize()
                        self.session = session
                        self._mcp_session_id_getter = _get_session_id  # KIWI-RESILIENCE
                        await self._discover_tools()
"""

A3_OLD = """                async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                    self.initialize_result = await session.initialize()
                    self.session = session
                    await self._discover_tools()
"""
A3_NEW = """                async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                    self.initialize_result = await session.initialize()
                    self.session = session
                    self._mcp_session_id_getter = _get_session_id  # KIWI-RESILIENCE
                    await self._discover_tools()
"""

# --------------------------------------------------------------------------
# 3. Soft keepalive + stateless interval (_wait_for_lifecycle_event).
# --------------------------------------------------------------------------
A4_OLD = """        _KEEPALIVE_INTERVAL = 180  # 3 minutes

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
"""
A4_NEW = '''        _KEEPALIVE_INTERVAL = 180  # 3 minutes

        # KIWI-RESILIENCE (B): a stateless Streamable-HTTP server issues no
        # Mcp-Session-Id and keeps no connection state between calls, so the
        # keepalive cannot detect anything — it can only *cause* damage by
        # tripping over a transient 5xx. Back it off hard for those. stdio
        # servers never reach this branch (_is_http() is False), so their
        # 180s behaviour is bit-for-bit unchanged.
        if self._is_http():
            _sid_getter = getattr(self, "_mcp_session_id_getter", None)
            if _sid_getter is not None:
                try:
                    _stateless = _sid_getter() is None
                except Exception:
                    _stateless = False
                if _stateless:
                    _KEEPALIVE_INTERVAL = _STATELESS_KEEPALIVE_INTERVAL
                    logger.debug(
                        "MCP server '%s': stateless HTTP (no Mcp-Session-Id) — "
                        "keepalive interval raised to %ds",
                        self.name, _KEEPALIVE_INTERVAL,
                    )

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
'''

A5_OLD = """                if self.session:
                    try:
                        await asyncio.wait_for(
                            self.session.list_tools(),
                            timeout=30.0,
                        )
                    except Exception as exc:
                        logger.warning(
                            "MCP server '%s' keepalive failed, "
                            "triggering reconnect: %s",
                            self.name, exc,
                        )
                        self._reconnect_event.set()
                        break
"""
A5_NEW = '''                if self.session:
                    try:
                        await asyncio.wait_for(
                            self.session.list_tools(),
                            timeout=30.0,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # KIWI-RESILIENCE (B+C): one failed keepalive is NOT
                        # proof of a dead session — Kiwi answers 503 now and
                        # then. Retry once after 10s and only tear the session
                        # down if the retry also fails. Log the real
                        # sub-exception, not the opaque ExceptionGroup str().
                        logger.warning(
                            "MCP server '%s' keepalive failed (%s), "
                            "retrying once in 10s",
                            self.name, _kiwi_exc_detail(exc),
                            exc_info=True,
                        )
                        _ka_recovered = False
                        try:
                            await asyncio.sleep(10)
                            if self.session:
                                await asyncio.wait_for(
                                    self.session.list_tools(),
                                    timeout=30.0,
                                )
                                _ka_recovered = True
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc2:
                            logger.warning(
                                "MCP server '%s' keepalive retry failed (%s), "
                                "triggering reconnect",
                                self.name, _kiwi_exc_detail(exc2),
                                exc_info=True,
                            )
                        if _ka_recovered:
                            logger.info(
                                "MCP server '%s': keepalive recovered on retry "
                                "— session kept", self.name,
                            )
                        else:
                            self._reconnect_event.set()
                            break
'''

# --------------------------------------------------------------------------
# 4. THE FIX (A): reset the retry budget after a session that actually lived.
# --------------------------------------------------------------------------
A6_OLD = """                retries += 1
                if retries > _MAX_RECONNECT_RETRIES:
                    logger.warning(
                        "MCP server '%s' failed after %d reconnection attempts, "
                        "giving up: %s",
                        self.name, _MAX_RECONNECT_RETRIES, exc,
                    )
                    return

                logger.warning(
                    "MCP server '%s' connection lost (attempt %d/%d), "
                    "reconnecting in %.0fs: %s",
                    self.name, retries, _MAX_RECONNECT_RETRIES,
                    backoff, exc,
                )
"""
A6_NEW = '''                # KIWI-RESILIENCE (A): `retries`/`backoff` are initialized
                # once, above `while True`, and were never reset after a
                # successful reconnect — so the 5 lives granted by
                # _MAX_RECONNECT_RETRIES were spent CUMULATIVELY over the whole
                # process lifetime. Five unrelated blips across three days
                # exhausted them and the server was dead until a restart.
                # A session that survived >= _SESSION_HEALTHY_SECONDS proves
                # the endpoint is healthy, so the next failure is a new
                # incident and deserves a full, fresh budget.
                _lived = None
                if self._session_started_at is not None:
                    _lived = _kiwi_time.monotonic() - self._session_started_at
                if _lived is not None and _lived >= _SESSION_HEALTHY_SECONDS:
                    if retries:
                        logger.info(
                            "MCP server '%s': session was up %.0fs before "
                            "failing — resetting reconnect budget "
                            "(was %d/%d used)",
                            self.name, _lived, retries, _MAX_RECONNECT_RETRIES,
                        )
                    retries = 0
                    backoff = 1.0
                self._session_started_at = None

                retries += 1
                if retries > _MAX_RECONNECT_RETRIES:
                    logger.warning(
                        "MCP server '%s' failed after %d reconnection attempts, "
                        "giving up: %s",
                        self.name, _MAX_RECONNECT_RETRIES,
                        _kiwi_exc_detail(exc),
                        exc_info=True,
                    )
                    return

                logger.warning(
                    "MCP server '%s' connection lost (attempt %d/%d), "
                    "reconnecting in %.0fs: %s",
                    self.name, retries, _MAX_RECONNECT_RETRIES,
                    backoff, _kiwi_exc_detail(exc),
                    exc_info=True,
                )
'''

REPLACEMENTS = [
    ("constants + _kiwi_exc_detail helper", A0_OLD, A0_NEW),
    ("__slots__ declaration", A1S_OLD, A1S_NEW),
    ("__init__ attrs", A1_OLD, A1_NEW),
    ("session-id getter (new HTTP API)", A2_OLD, A2_NEW),
    ("session-id getter (legacy HTTP API)", A3_OLD, A3_NEW),
    ("stateless keepalive interval", A4_OLD, A4_NEW),
    ("soft keepalive (retry once)", A5_OLD, A5_NEW),
    ("retry-budget reset + real exception in logs", A6_OLD, A6_NEW),
]

for label, old, new in REPLACEMENTS:
    n = src.count(old)
    if n != 1:
        print("ANCHOR '%s' found %d times (want exactly 1) — ABORTING, "
              "no changes written" % (label, n))
        sys.exit(1)
    src = src.replace(old, new, 1)
    print("  anchor OK: %s" % label)

# --------------------------------------------------------------------------
# 5. Stamp the session-establishment time at every `self.session = session`
#    (stdio, SSE, new HTTP, legacy HTTP — 4 sites, all indentations).
#    Done last so it does not disturb the exact-match anchors above.
# --------------------------------------------------------------------------
_SESS_RE = re.compile(r"^([ \t]+)self\.session = session$", re.MULTILINE)
_hits = len(_SESS_RE.findall(src))
if _hits != 4:
    print("expected 4 `self.session = session` sites, found %d — ABORTING, "
          "no changes written" % _hits)
    sys.exit(1)
src = _SESS_RE.sub(
    lambda m: "%sself.session = session\n%sself._session_started_at = "
              "_kiwi_time.monotonic()  # KIWI-RESILIENCE" % (m.group(1), m.group(1)),
    src,
)
print("  anchor OK: session-start timestamp (%d sites)" % _hits)

if src == orig:
    print("nothing changed — ABORTING")
    sys.exit(1)

bak = F + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(time.time_ns() % 10**9)
shutil.copy2(F, bak)
print("backup:", bak)

open(F, "w").write(src)
try:
    py_compile.compile(F, doraise=True)
    print("mcp_tool.py py_compile OK — KIWI-RESILIENCE applied (A+B+C)")
except Exception as e:
    shutil.copy2(bak, F)
    print("COMPILE FAILED — reverted from backup:", e)
    sys.exit(1)
