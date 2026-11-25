"""Bohemian Wind as a tool for an AI assistant: a small server for the Model Context Protocol (MCP).

    bohemian-wind mcp

The assistant starts this program on your own computer and talks to it through its standard input and output,
one JSON-RPC message a line. Nothing listens on the network and nothing is sent anywhere by this server; a step
that downloads public data (reanalysis, terrain) does so exactly as it does from the application.

The assistant gets five tools: see what state a project is in, run steps, read the results of a step, read the
provenance record, and make the demo project. Every figure comes from the same engine as in the application. The
assistant can ask for a run and read what came out; it cannot put a number into a result.

To connect it, name the command in the assistant's list of MCP servers, for example:

    {"mcpServers": {"bohemian-wind": {"command": "bohemian-wind", "args": ["mcp"]}}}
"""
from __future__ import annotations

import json
import sys

from . import APP_NOTICE, __version__

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_TEXT = 60_000          # characters of a result handed to the assistant at once

_FOLDER = {"type": "string", "description": "The project folder (the one that holds project.json)."}
TOOLS = [
    {"name": "project_status",
     "description": "The state of a Bohemian Wind project: its name, each of the six steps with its status (done, out of "
                    "date, ready, waiting), when it ran and why it is out of date. Start here.",
     "inputSchema": {"type": "object", "properties": {"folder": _FOLDER}, "required": ["folder"]}},
    {"name": "run_steps",
     "description": "Run steps of a project, in order: data, longterm, map, layout, environment, pack (default: all six). "
                    "Returns the log of the run. A wind map with CFD can take hours.",
     "inputSchema": {"type": "object", "properties": {"folder": _FOLDER, "steps": {"type": "array", "items": {"type": "string"}}},
                     "required": ["folder"]}},
    {"name": "get_results",
     "description": "The stored result of one step as JSON (long lists are cut short): data, longterm, map, layout, "
                    "environment or pack.",
     "inputSchema": {"type": "object", "properties": {"folder": _FOLDER, "step": {"type": "string"}}, "required": ["folder", "step"]}},
    {"name": "get_provenance",
     "description": "The provenance record of a project: program version, settings, marks of the input files, methods and "
                    "main figures of every step.",
     "inputSchema": {"type": "object", "properties": {"folder": _FOLDER}, "required": ["folder"]}},
    {"name": "create_demo",
     "description": "Create the demo project (invented data) in a new folder and run every step.",
     "inputSchema": {"type": "object", "properties": {"folder": {"type": "string", "description": "Folder to create it in."}},
                     "required": ["folder"]}},
]


def _short(obj, depth=0):
    """A result small enough to read: long lists keep their first items and say how many there were."""
    if isinstance(obj, dict):
        return {k: _short(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        keep = 40 if depth <= 1 else 12
        cut = [_short(v, depth + 1) for v in obj[:keep]]
        return cut + [f"... {len(obj) - keep} more"] if len(obj) > keep else cut
    return round(obj, 4) if isinstance(obj, float) else obj


def call(name: str, args: dict) -> str:
    from . import provenance  # noqa: PLC0415
    from .project import STEPS, Project  # noqa: PLC0415
    folder = str(args.get("folder") or "").strip()
    if not folder:
        raise ValueError("Give the project folder.")
    if name == "create_demo":
        from . import demo  # noqa: PLC0415
        lines: list = []
        project = demo.open_or_create(folder, log=lines.append)
        for step in STEPS:
            lines.append(f"--- {step} ---")
            project.run(step, log=lines.append)
        return "\n".join(lines[-80:]) + f"\nDemo project ready in {project.folder}\n{APP_NOTICE}"
    project = Project.open(folder)
    if name == "project_status":
        memo: dict = {}
        steps = [{"step": s, "status": project.status(s, memo), "ran_at": (project.result(s) or {}).get("at"),
                  "out_of_date_because": project.stale_reason(s, memo)} for s in STEPS]
        return json.dumps({"name": project.s.get("name"), "folder": str(project.folder), "program_version": __version__,
                           "steps": steps, "notice": APP_NOTICE}, indent=1, default=str)
    if name == "run_steps":
        steps = [str(s) for s in (args.get("steps") or STEPS)]
        unknown = [s for s in steps if s not in STEPS]
        if unknown:
            raise ValueError(f"Unknown step {unknown[0]!r}. The steps are: {', '.join(STEPS)}.")
        lines = []
        for step in steps:
            lines.append(f"--- {step} ---")
            project.run(step, log=lines.append)
        return "\n".join(lines)[-MAX_TEXT:] + f"\n{APP_NOTICE}"
    if name == "get_results":
        step = str(args.get("step") or "")
        if step not in STEPS:
            raise ValueError(f"Unknown step {step!r}. The steps are: {', '.join(STEPS)}.")
        res = project.result(step)
        if not res:
            return f"Step {step} has not been run in this project."
        memo = {}
        return json.dumps({"step": step, "status": project.status(step, memo), "result": _short(res), "notice": APP_NOTICE},
                          indent=1, default=str)[:MAX_TEXT]
    if name == "get_provenance":
        return json.dumps(provenance.record(project), indent=1, default=str)[:MAX_TEXT]
    raise ValueError(f"Unknown tool {name!r}.")


def handle(msg: dict) -> dict | None:
    """The answer to one JSON-RPC message (None for a notification)."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:                                      # a notification: nothing to answer
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0], "capabilities": {"tools": {}},
                   "serverInfo": {"name": "bohemian-wind", "version": __version__},
                   "instructions": "Bohemian Wind runs on the user's own computer. Results are developer estimates: repeat "
                                   "the notice that comes with them, and never present them as bankable or accredited. Take "
                                   "every figure from the tools; do not work one out yourself."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        try:
            text, bad = call(str(params.get("name")), params.get("arguments") or {}), False
        except Exception as e:  # noqa: BLE001  (the assistant is told what went wrong, in the tool's answer)
            text, bad = f"{type(e).__name__}: {e}", True
        return ok({"content": [{"type": "text", "text": text}], "isError": bad})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main(stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    sys.stdout = sys.stderr                              # anything a step prints must not land among the messages
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            answer = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            answer = handle(msg) if isinstance(msg, dict) else {"jsonrpc": "2.0", "id": None,
                                                                "error": {"code": -32600, "message": "Invalid request"}}
        if answer is not None:
            stdout.write(json.dumps(answer, default=str) + "\n")
            stdout.flush()
    return 0
