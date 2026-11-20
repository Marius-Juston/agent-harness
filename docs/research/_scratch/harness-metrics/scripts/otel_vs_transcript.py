"""Join otlp_sink.py api_request events to the session transcripts by request id (#502): does OTel's per-request
usage match what the transcripts log, do event agent_ids name the subagent transcript files, and are prompt and
response attributes redacted without OTEL_LOG_USER_PROMPTS? Prints counts and token sums only.

Usage: python -I otel_vs_transcript.py <sink.jsonl> <transcript project dir>
"""

import json
import sys
from collections import Counter
from pathlib import Path

FIELDS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_creation_tokens": "cache_creation_input_tokens",
}


def attrs(kvs: list) -> dict:
    out = {}
    for kv in kvs or []:
        v = kv["value"]
        out[kv["key"]] = next((v[k] for k in ("stringValue", "intValue", "doubleValue", "boolValue") if k in v), None)
    return out


def events(path: str) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        for rl in json.loads(line)["payload"].get("resourceLogs", []):
            for sl in rl.get("scopeLogs", []):
                out += [attrs(lr.get("attributes")) for lr in sl.get("logRecords", [])]
    return out


def transcript_usage(project: Path) -> tuple[dict[str, dict], set[str]]:
    """requestId -> the largest logged usage per field (Claude Code repeats a message's usage on each block line)."""
    usage: dict[str, dict] = {}
    agents = set()
    for f in project.rglob("*.jsonl"):
        if f.parent.name == "subagents":
            agents.add(f.stem.removeprefix("agent-"))
        for line in f.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            msg = rec.get("message") or {}
            if rec.get("type") != "assistant" or not rec.get("requestId") or msg.get("model") == "<synthetic>":
                continue
            u = msg.get("usage") or {}
            row = usage.setdefault(rec["requestId"], dict.fromkeys(FIELDS, 0))
            for k, t in FIELDS.items():
                row[k] = max(row[k], int(u.get(t) or 0))
    return usage, agents


def main() -> None:
    evs = events(sys.argv[1])
    usage, agents = transcript_usage(Path(sys.argv[2]))
    reqs = [e for e in evs if e.get("event.name") == "api_request"]
    otel, logged = Counter(), Counter()
    joined = 0
    for e in reqs:
        t = usage.get(e.get("request_id"))
        if t is None:
            continue
        joined += 1
        for k in FIELDS:
            otel[k] += int(e.get(k) or 0)
            logged[k] += t[k]
    print(
        f"api_request events: {len(reqs)}, joined to a transcript requestId: {joined}, transcript requests: {len(usage)}"
    )
    for k in FIELDS:
        print(
            f"  {k:<24} otel {otel[k]:>8}  transcript {logged[k]:>8}  ratio {otel[k] / logged[k] if logged[k] else 0:.2f}"
        )
    ev_agents = {e["agent_id"] for e in evs if e.get("agent_id")}
    print(f"event agent_ids: {len(ev_agents)}, matching a subagents/agent-<id>.jsonl file: {len(ev_agents & agents)}")
    for name, key in (("user_prompt", "prompt"), ("user_prompt", "prompt_text"), ("assistant_response", "response")):
        vals = Counter(str(e.get(key))[:24] for e in evs if e.get("event.name") == name)
        print(
            f"  {name}.{key}: {dict(vals) if all(v.startswith('<') for v in vals) else f'{len(vals)} non-placeholder values'}"
        )


if __name__ == "__main__":
    main()
