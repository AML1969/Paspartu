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

B. KEEPALIVE IS PURE DAMAGE ON STATELESS HTTP  (B2: now fully disabled).
   `_wait_for_lifecycle_event()` fires `session.list_tools()` every 180s to
   keep TCP warm. Kiwi is a *stateless* Streamable-HTTP server: it never
   issues an `Mcp-Session-Id`, holds no per-connection state, keeps no socket
   open and opens no SSE stream. There is literally nothing to keep alive —
   but every keepalive POST is another chance to catch one of Kiwi's
   occasional 503s, and a 503 inside that POST tears the anyio task group
   down and kills the session. The keepalive is therefore the *only* source
   of idle-time disconnects for this server.
   The first iteration of this patch (B) merely stretched the interval to
   900s; in production that still produced the very failure it was meant to
   avoid ("session was up 900s before failing — resetting reconnect budget").
   Fix (B2): for stateless HTTP the keepalive is switched OFF entirely —
   `_KEEPALIVE_INTERVAL = None`, so `asyncio.wait()` just blocks on the
   shutdown/reconnect events with no timeout (no polling, no leaked tasks, no
   idle traffic upstream). One INFO line per connect records it.
   Gating — these servers keep the original 180s keepalive:
     * stdio (e.g. `hotels`)            -> `_is_http()` is False
     * SSE                              -> own branch, never captures the
                                           session-id getter
     * stateful HTTP (issues a session id) -> getter returns a string
   Only "HTTP transport AND session id is None" turns the keepalive off.
   The soft keepalive (one failed `list_tools()` is retried once after 10s
   before the session is torn down) stays in place for everyone who still has
   a keepalive.

C. THE REAL ERROR WAS INVISIBLE.
   Failures are logged with a bare `%s` on an ExceptionGroup, whose str() is
   the useless "unhandled errors in a TaskGroup (1 sub-exception)". The 503
   underneath was never printed. Fix: `_kiwi_exc_detail()` recurses into
   `.exceptions`, plus `exc_info=True` on the reconnect-path warnings.

D. THE INITIAL CONNECT HAD ITS OWN, SEPARATE, TINY BUDGET.
   `_MAX_INITIAL_CONNECT_RETRIES = 3` is checked in a branch of its own
   (`if not self._ready.is_set()`), completely disjoint from the reconnect
   budget that (A) repairs. If the upstream answers 503 on the first three
   tries — precisely what Kiwi does now and then while the gateway is
   booting — `run()` logs "failed initial connection after 3 attempts,
   giving up" and RETURNS. The name never enters `_servers`, no tools are
   registered, and the server stays dead for the whole process lifetime.
   That is the real, observed failure: Ruslan's profile came up with 6 tools
   from 1 server instead of 12 from 2; Igor's docker copy did the same.
   Fix: never give up. Report the failure to `start()` (so gateway startup
   is not blocked — it boots without the server, exactly as before) but keep
   the task alive in a *background late-connect loop*: exponential backoff
   from 30s to a 10-minute ceiling, up to 288 attempts (~2 days). When the
   upstream finally answers, `_discover_tools()` calls `_kiwi_late_register()`
   which
     * puts the server into `_servers`,
     * registers its tools via the very same `_register_server_tools()` the
       normal startup path uses (registry generation bumps, so the memoized
       `get_tool_definitions()` recomputes), and
     * refreshes `agent.tools` / `agent.valid_tool_names` of every live
       gateway session (via `gateway.run._gateway_runner_ref` -> `_agent_cache`,
       the same mechanism `/reload-mcp` uses), so the tools become usable on
       the next turn of an *already running* conversation — not only in a
       fresh session.
   Auth errors still stop the loop (bad credentials do not heal themselves),
   and a hard-broken stdio command cannot busy-loop: with a 30s floor and a
   600s ceiling it is at most one attempt (and one log line) per window.

NB: MCPServerTask declares __slots__ — every new attribute is added there as
well, otherwise assigning it raises AttributeError and every MCP server
(including stdio ones) fails to connect.

Touches only tools/mcp_tool.py. Behaviour for stdio and SSE transports is
unchanged.
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

# Keepalive interval for *stateless* Streamable-HTTP servers — servers that
# return no Mcp-Session-Id, hold no connection state, keep no socket open and
# open no SSE stream (e.g. mcp.kiwi.com).
#
# None == keepalive DISABLED. There is no connection to keep warm, so a
# periodic `list_tools()` cannot detect anything; it can only *cause* damage
# by tripping over a transient upstream 5xx, which tears down the anyio task
# group and kills an otherwise usable session. With None,
# `_wait_for_lifecycle_event()` blocks on the shutdown/reconnect events with
# no timeout. (Put an int here to go back to a long-interval keepalive.)
_STATELESS_KEEPALIVE_INTERVAL = None


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


# --- KIWI-RESILIENCE (D): never give up on the *initial* connect -----------
#
# Backoff for the background late-connect loop that takes over once the tiny
# initial budget (_MAX_INITIAL_CONNECT_RETRIES) is spent. The 30s floor is what
# stops a hard-broken stdio command (missing binary, fails in milliseconds)
# from turning "retry forever" into a busy loop; the 600s ceiling keeps a
# long-dead upstream down to 6 attempts (and 6 log lines) per hour.
_LATE_CONNECT_INITIAL_BACKOFF = 30.0
_LATE_CONNECT_MAX_BACKOFF = 600.0      # 10 minutes
# 0 == unlimited. 288 attempts at the ceiling is roughly two days, after which
# something is wrong that a retry will not fix.
_LATE_CONNECT_MAX_ATTEMPTS = 288

# Servers stuck in the late-connect loop. They are deliberately NOT in
# `_servers` (they have no session and no tools yet), so this dict is the only
# strong reference keeping their MCPServerTask — and therefore their asyncio
# Task — alive: asyncio holds only a weak reference to a running task.
# Guarded by `_lock`, like `_servers`.
_late_servers: dict = {}


def _kiwi_refresh_cached_agent_tools() -> int:
    """Re-read the tool catalog into every live gateway agent.

    The gateway caches one AIAgent per session (`_agent_cache`) and each agent
    snapshots `tools` / `valid_tool_names` at construction time — a tool that
    appears in the registry later is invisible to a conversation that is
    already running (conversation_loop rejects any call whose name is not in
    `valid_tool_names`). `/reload-mcp` solves this by rebuilding those two
    attributes in place; a late MCP registration needs exactly the same thing.

    Safe no-op outside the gateway (CLI, cron, ACP): the weakref is unset, so
    we return 0. Never raises.
    """
    try:
        from gateway import run as _gw_run
        _ref = getattr(_gw_run, "_gateway_runner_ref", None)
        runner = _ref() if callable(_ref) else None
    except Exception:
        return 0
    if runner is None:
        return 0
    cache = getattr(runner, "_agent_cache", None)
    if not cache:
        return 0
    try:
        from model_tools import get_tool_definitions
    except Exception:
        return 0
    from contextlib import nullcontext as _kiwi_nullcontext
    cache_lock = getattr(runner, "_agent_cache_lock", None)
    updated = 0
    try:
        with (cache_lock if cache_lock is not None else _kiwi_nullcontext()):
            for _key, _entry in list(cache.items()):
                try:
                    _agent = _entry[0] if isinstance(_entry, tuple) else _entry
                    if _agent is None:
                        continue
                    _defs = get_tool_definitions(
                        enabled_toolsets=getattr(_agent, "enabled_toolsets", None),
                        disabled_toolsets=getattr(_agent, "disabled_toolsets", None),
                        quiet_mode=True,
                    )
                    _agent.tools = _defs
                    _agent.valid_tool_names = (
                        {t["function"]["name"] for t in _defs} if _defs else set()
                    )
                    updated += 1
                except Exception:
                    continue
    except Exception:
        return updated
    return updated


def _kiwi_late_register(server) -> None:
    """Register a server that only came up AFTER gateway startup.

    Mirrors `_discover_and_register_server()` exactly (which is what the normal
    startup path runs): `_servers[name] = server` first — `_make_check_fn()`
    resolves the tool's liveness through `_servers` — then
    `_register_server_tools()`, which bumps the registry generation and thus
    invalidates the memoized `get_tool_definitions()`.

    NB: `_lock` is a plain (non-reentrant) threading.Lock and
    `_register_server_tools()` takes it internally, so it must NOT be called
    while we hold it.
    """
    name = server.name
    try:
        with _lock:
            _servers[name] = server
        try:
            registered = _register_server_tools(name, server, server._config)
        except Exception:
            with _lock:
                _servers.pop(name, None)
            raise
        server._registered_tool_names = list(registered)
        transport_type = "HTTP" if server._is_http() else "stdio"
        logger.info(
            "MCP server '%s' (%s): LATE registration OK (came up after %d "
            "failed background attempt(s)) — registered %d tool(s): %s",
            name, transport_type, server._late_attempts,
            len(registered), ", ".join(registered),
        )
        server._error = None
        server._late_mode = False
        server._late_attempts = 0
        with _lock:
            _late_servers.pop(name, None)
        _refreshed = _kiwi_refresh_cached_agent_tools()
        if _refreshed:
            logger.info(
                "MCP server '%s': tool list refreshed in %d live agent "
                "session(s) — its tools are callable from the next turn on",
                name, _refreshed,
            )
    except Exception:
        # Leave _late_mode set: the next reconnect will retry the registration.
        logger.exception("MCP server '%s': late registration FAILED", name)
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
        "_late_mode", "_late_attempts",
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
        # KIWI-RESILIENCE (D): True while the server is stuck in the background
        # late-connect loop (initial budget spent, gateway started without it).
        self._late_mode: bool = False
        self._late_attempts: int = 0
'''

# --------------------------------------------------------------------------
# 2. Capture the session-id getter in BOTH HTTP branches (new + legacy API).
#    The two blocks differ only in indentation, which makes each unique.
#    NB: the SSE branch deliberately does NOT set it — an SSE session IS
#    stateful on the server side and keeps its 180s keepalive.
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
# 3a. Keepalive OFF for stateless HTTP (_wait_for_lifecycle_event).
# --------------------------------------------------------------------------
A4_OLD = """        _KEEPALIVE_INTERVAL = 180  # 3 minutes

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
"""
A4_NEW = '''        _KEEPALIVE_INTERVAL = 180  # 3 minutes

        # KIWI-RESILIENCE (B2): a *stateless* Streamable-HTTP server issues no
        # Mcp-Session-Id, keeps no connection state, holds no socket open and
        # opens no SSE stream — every request is a standalone POST. A periodic
        # keepalive therefore keeps nothing alive; it only adds a recurring
        # chance to catch a transient upstream 5xx, which tears down the anyio
        # task group and kills an otherwise perfectly usable session. So it is
        # switched off entirely for those servers (_STATELESS_KEEPALIVE_INTERVAL
        # is None => the asyncio.wait() below has no timeout and simply blocks
        # on the lifecycle events).
        #
        # Everyone else keeps the original 180s keepalive:
        #   * stdio (e.g. `hotels`)               -> _is_http() is False
        #   * SSE                                 -> own branch, never sets the
        #                                            session-id getter
        #   * stateful HTTP (has an Mcp-Session-Id) -> getter returns a string
        if self._is_http():
            _sid_getter = getattr(self, "_mcp_session_id_getter", None)
            if _sid_getter is not None:
                try:
                    _stateless = _sid_getter() is None
                except Exception:
                    _stateless = False
                if _stateless:
                    _KEEPALIVE_INTERVAL = _STATELESS_KEEPALIVE_INTERVAL
                    if _KEEPALIVE_INTERVAL is None:
                        logger.info(
                            "MCP server '%s': stateless HTTP (no Mcp-Session-Id)"
                            " — keepalive DISABLED (nothing to keep warm; idle"
                            " pings would only risk a transient 5xx)", self.name,
                        )
                    else:
                        logger.info(
                            "MCP server '%s': stateless HTTP (no Mcp-Session-Id)"
                            " — keepalive interval raised to %ss",
                            self.name, _KEEPALIVE_INTERVAL,
                        )

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
'''

# --------------------------------------------------------------------------
# 3b. The wait itself. With _KEEPALIVE_INTERVAL = None asyncio.wait() has no
#     timeout, so it returns only when shutdown/reconnect fires: no busy loop,
#     no leaked tasks (the existing `finally` cancels both), CancelledError
#     propagates unchanged. shutdown() sets BOTH events, so it still unblocks
#     us immediately.
# --------------------------------------------------------------------------
A4B_OLD = """                done, _pending = await asyncio.wait(
                    {shutdown_task, reconnect_task},
                    timeout=_KEEPALIVE_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    break
"""
A4B_NEW = """                # KIWI-RESILIENCE (B2): when the keepalive is disabled
                # (_KEEPALIVE_INTERVAL is None, i.e. stateless HTTP) this wait
                # has no timeout and can only return with a completed task, so
                # we break out on the lifecycle event and never fall through to
                # the keepalive below. Nothing spins, nothing leaks.
                done, _pending = await asyncio.wait(
                    {shutdown_task, reconnect_task},
                    timeout=_KEEPALIVE_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    break
                if _KEEPALIVE_INTERVAL is None:
                    # Unreachable (timeout=None never returns empty), but keeps
                    # the loop honest if asyncio ever changes its mind.
                    continue
"""

# --------------------------------------------------------------------------
# 3c. Soft keepalive: one failed list_tools() no longer kills the session.
#     Reached only by transports that still HAVE a keepalive (stdio, SSE,
#     stateful HTTP).
# --------------------------------------------------------------------------
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
                        # proof of a dead session — upstreams answer 503 now and
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

# --------------------------------------------------------------------------
# 4d. PART D — the initial connect never gives up.
#
# D1: run() gets its own `late_backoff` local, kept strictly separate from the
#     reconnect `backoff` so a long late-backoff can never leak into the
#     normal reconnect path after the server finally comes up.
# --------------------------------------------------------------------------
D1_OLD = """        retries = 0
        initial_retries = 0
        backoff = 1.0
"""
D1_NEW = """        retries = 0
        initial_retries = 0
        backoff = 1.0
        # KIWI-RESILIENCE (D): backoff of the background late-connect loop.
        # Deliberately a separate local from `backoff` above: after a late
        # connect finally succeeds, the normal reconnect path must start from
        # its own small backoff, not from the 10-minute late-connect ceiling.
        late_backoff = _LATE_CONNECT_INITIAL_BACKOFF
"""

# --------------------------------------------------------------------------
# D2: the late-connect loop itself. Placed at the very TOP of `except
#     Exception` so it takes precedence over BOTH the initial branch (`if not
#     self._ready.is_set()` — `_ready` IS set in late mode) and the reconnect
#     branch (whose 5-attempt budget would end in a real "giving up").
# --------------------------------------------------------------------------
D2_OLD = """            except Exception as exc:
                self.session = None

                # If this is the first connection attempt, retry with backoff
"""
D2_NEW = '''            except Exception as exc:
                self.session = None

                # KIWI-RESILIENCE (D): background late-connect loop. We land
                # here after the initial budget is spent: the gateway has
                # already started without this server (`_ready` and `_error`
                # are set, the name is NOT in `_servers`, no tools registered).
                # Keep trying — quietly, slowly, and for a long time.
                if self._late_mode:
                    if self._shutdown_event.is_set():
                        with _lock:
                            _late_servers.pop(self.name, None)
                        return
                    if _is_auth_error(exc):
                        # Credentials do not heal themselves; retrying forever
                        # would only hammer the upstream. Same policy as the
                        # initial-connect branch below.
                        logger.warning(
                            "MCP server '%s': background connect hit an auth "
                            "error — stopping the retry loop: %s",
                            self.name, _kiwi_exc_detail(exc),
                        )
                        self._error = exc
                        with _lock:
                            _late_servers.pop(self.name, None)
                        return
                    self._late_attempts += 1
                    if (_LATE_CONNECT_MAX_ATTEMPTS
                            and self._late_attempts > _LATE_CONNECT_MAX_ATTEMPTS):
                        logger.error(
                            "MCP server '%s': still unreachable after %d "
                            "background attempts — giving up for real. Fix the "
                            "server or the config, then /reload-mcp (or restart "
                            "Hermes). Last error: %s",
                            self.name, _LATE_CONNECT_MAX_ATTEMPTS,
                            _kiwi_exc_detail(exc),
                        )
                        self._error = exc
                        with _lock:
                            _late_servers.pop(self.name, None)
                        return
                    logger.warning(
                        "MCP server '%s': background connect attempt %d/%d "
                        "failed, next try in %.0fs: %s",
                        self.name, self._late_attempts,
                        _LATE_CONNECT_MAX_ATTEMPTS, late_backoff,
                        _kiwi_exc_detail(exc),
                    )
                    # Sleep on the shutdown event rather than asyncio.sleep():
                    # shutdown() must not have to wait out a 10-minute backoff
                    # (it would time out and hard-cancel the task instead).
                    # The 30s floor + 600s ceiling is also what makes an
                    # instantly-failing stdio command (missing binary) cost one
                    # attempt and one log line per window instead of spinning.
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(), timeout=late_backoff,
                        )
                    except asyncio.TimeoutError:
                        pass
                    if self._shutdown_event.is_set():
                        with _lock:
                            _late_servers.pop(self.name, None)
                        return
                    late_backoff = min(
                        late_backoff * 2, _LATE_CONNECT_MAX_BACKOFF
                    )
                    continue

                # If this is the first connection attempt, retry with backoff
'''

# --------------------------------------------------------------------------
# D3: the actual bug — "giving up" on the initial connect. Enter late mode
#     instead of returning. `_error` + `_ready` are still set, so `start()`
#     raises exactly as before and gateway startup is never blocked: it boots
#     without the server, and the server joins later, on its own.
# --------------------------------------------------------------------------
D3_OLD = """                    initial_retries += 1
                    if initial_retries > _MAX_INITIAL_CONNECT_RETRIES:
                        logger.warning(
                            "MCP server '%s' failed initial connection after "
                            "%d attempts, giving up: %s",
                            self.name, _MAX_INITIAL_CONNECT_RETRIES, exc,
                        )
                        self._error = exc
                        self._ready.set()
                        return
"""
D3_NEW = '''                    initial_retries += 1
                    if initial_retries > _MAX_INITIAL_CONNECT_RETRIES:
                        # KIWI-RESILIENCE (D): this used to `return` — and that
                        # single `return` is what killed Kiwi for whole days.
                        # The initial budget is separate from (and much smaller
                        # than) the reconnect budget: 3 retries, ~7 seconds. A
                        # transient 503 while the gateway boots was enough to
                        # lose the server until someone restarted Hermes by hand.
                        #
                        # Now: report the failure to `start()` (so the gateway
                        # starts on time, without this server — unchanged
                        # behaviour) but keep the task alive and retry in the
                        # background. `_kiwi_late_register()` (called from
                        # `_discover_tools`) registers the tools into the LIVE
                        # gateway once the upstream answers.
                        self._error = exc
                        self._late_mode = True
                        with _lock:
                            # Strong ref: _servers does not hold us (we never
                            # registered) and asyncio only weak-refs a task.
                            _late_servers[self.name] = self
                        self._ready.set()
                        logger.warning(
                            "MCP server '%s': initial connection failed after "
                            "%d attempts — NOT giving up. Retrying in the "
                            "background (backoff %.0fs -> %.0fs, up to %d "
                            "attempts); its tools are unavailable until it "
                            "answers, then they are registered automatically. "
                            "Last error: %s",
                            self.name, initial_retries,
                            _LATE_CONNECT_INITIAL_BACKOFF,
                            _LATE_CONNECT_MAX_BACKOFF,
                            _LATE_CONNECT_MAX_ATTEMPTS,
                            _kiwi_exc_detail(exc),
                        )
                        try:
                            await asyncio.wait_for(
                                self._shutdown_event.wait(),
                                timeout=late_backoff,
                            )
                        except asyncio.TimeoutError:
                            pass
                        if self._shutdown_event.is_set():
                            with _lock:
                                _late_servers.pop(self.name, None)
                            return
                        late_backoff = min(
                            late_backoff * 2, _LATE_CONNECT_MAX_BACKOFF
                        )
                        continue
'''

# --------------------------------------------------------------------------
# D4: the late-registration hook. `_discover_tools()` is the single point every
#     transport (stdio, SSE, new HTTP, legacy HTTP) passes through right after
#     `session.initialize()` and right before `_ready.set()`, so one hook here
#     covers them all. No-op unless the server is in late mode, so the normal
#     startup path is bit-for-bit unchanged.
# --------------------------------------------------------------------------
D4_OLD = """        self._tools = (
            tools_result.tools
            if hasattr(tools_result, "tools")
            else []
        )
"""
D4_NEW = """        self._tools = (
            tools_result.tools
            if hasattr(tools_result, "tools")
            else []
        )
        # KIWI-RESILIENCE (D): this server came up through the background
        # late-connect loop, i.e. the gateway started without it and nothing
        # ever registered its tools. Do it now — registry + live sessions.
        if self._late_mode:
            _kiwi_late_register(self)
"""

# --------------------------------------------------------------------------
# D5: shutdown_mcp_servers() must also stop tasks that are still retrying.
#     They are not in `_servers`, so without this /reload-mcp would leak a
#     retrying task that could later resurrect a server the user just removed
#     — and a second task would be created for the same name.
# --------------------------------------------------------------------------
D5_OLD = """    with _lock:
        servers_snapshot = list(_servers.values())

    # Fast path: nothing to shut down.
"""
D5_NEW = """    with _lock:
        servers_snapshot = list(_servers.values())
        # KIWI-RESILIENCE (D): servers still stuck in the background
        # late-connect loop never made it into `_servers`, but their asyncio
        # Task is very much alive and retrying. Shut them down too, or
        # /reload-mcp leaks a task (and can end up with two tasks per name).
        # Their `shutdown()` returns promptly: the late loop sleeps on
        # `_shutdown_event`, not on asyncio.sleep().
        _late_snapshot = [
            s for s in _late_servers.values() if s not in servers_snapshot
        ]
        servers_snapshot.extend(_late_snapshot)
        _late_servers.clear()

    # Fast path: nothing to shut down.
"""

# --------------------------------------------------------------------------
# D6: register_mcp_servers() must not spawn a SECOND task for a name that is
#     already retrying in the background (it only skips names in `_servers`).
# --------------------------------------------------------------------------
D6_OLD = """            if k not in _servers and _parse_boolish(v.get("enabled", True), default=True)
"""
D6_NEW = """            # KIWI-RESILIENCE (D): `_late_servers` holds names whose task is
            # alive and retrying in the background but which are not (yet) in
            # `_servers`. Without this guard a second discovery pass would
            # start a duplicate task for the same server.
            if k not in _servers and k not in _late_servers
            and _parse_boolish(v.get("enabled", True), default=True)
"""

REPLACEMENTS = [
    ("constants + _kiwi_exc_detail helper", A0_OLD, A0_NEW),
    ("__slots__ declaration", A1S_OLD, A1S_NEW),
    ("__init__ attrs", A1_OLD, A1_NEW),
    ("session-id getter (new HTTP API)", A2_OLD, A2_NEW),
    ("session-id getter (legacy HTTP API)", A3_OLD, A3_NEW),
    ("keepalive OFF for stateless HTTP", A4_OLD, A4_NEW),
    ("lifecycle wait (timeout=None when keepalive off)", A4B_OLD, A4B_NEW),
    ("soft keepalive (retry once)", A5_OLD, A5_NEW),
    ("retry-budget reset + real exception in logs", A6_OLD, A6_NEW),
    ("D1 late_backoff local", D1_OLD, D1_NEW),
    ("D2 background late-connect loop", D2_OLD, D2_NEW),
    ("D3 initial connect never gives up", D3_OLD, D3_NEW),
    ("D4 late registration hook", D4_OLD, D4_NEW),
    ("D5 shutdown stops late-connect tasks", D5_OLD, D5_NEW),
    ("D6 no duplicate task for a retrying server", D6_OLD, D6_NEW),
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
    print("mcp_tool.py py_compile OK — KIWI-RESILIENCE applied (A+B2+C+D)")
except Exception as e:
    shutil.copy2(bak, F)
    print("COMPILE FAILED — reverted from backup:", e)
    sys.exit(1)
