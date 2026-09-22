#!/usr/bin/env python3
"""Call one tool on the Grafana MCP server (streamable HTTP) and print its text result.

  python3 assistant/mcp_call.py query_prometheus '{"datasourceUid":"grafanacloud-prom","expr":"up","queryType":"instant","startTime":"now-5m","endTime":"now"}'
MCP_URL defaults to http://localhost:8300/mcp (the CORTEX API owns 8000).
"""
import json
import os
import signal
import sys
import urllib.request

signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # play nicely with | head

MCP_URL = os.getenv("MCP_URL", "http://localhost:8300/mcp")
MCP_TIMEOUT = int(os.getenv("MCP_TIMEOUT", "180"))  # seconds; ask_assistant can take minutes


def call(name: str, args: dict) -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(MCP_URL, data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=MCP_TIMEOUT).read())
    except urllib.error.URLError as e:
        sys.exit(f"MCP server not reachable at {MCP_URL}: {e.reason}  (start it: make mcp, or make mcp-oss for the self-hosted stack)")
    if "error" in r:
        return "ERROR " + json.dumps(r["error"])
    return "\n".join(c.get("text", "") for c in r["result"].get("content", []))


def tools() -> list[str]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    req = urllib.request.Request(MCP_URL, data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    return [t["name"] for t in json.loads(urllib.request.urlopen(req, timeout=60).read())["result"]["tools"]]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    if sys.argv[1] == "--tools":
        names = tools(); print(len(names)); print("\n".join(names)); sys.exit(0)
    out = call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
    print(out[:limit])
