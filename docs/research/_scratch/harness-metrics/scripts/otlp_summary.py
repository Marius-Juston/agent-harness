"""Summarize an otlp_sink.py file (#502): per event and per metric, the attribute keys and the values of the keys
the metrics design needs. Prints no session id, request id or prompt text.

Usage: python -I otlp_summary.py <file.jsonl> [first_line] [last_line]   (1-based, inclusive; default: all lines)
"""

import json
import sys
from collections import Counter, defaultdict

SHOW = (
    "model",
    "query_source",
    "agent.name",
    "agent_type",
    "agent.source",
    "effort",
    "type",
    "start_type",
    "plugin.name",
    "skill.name",
    "is_built_in",
    "service.version",
    "app.version",
    "devkit.issue",
    "devkit.role",
)
NUMERIC = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "cache_creation_1h_tokens",
    "cache_creation_5m_tokens",
    "cost_usd",
    "duration_ms",
    "total_tokens",
    "total_tool_uses",
)


def value(v: dict) -> object:
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return v[k]
    return v.get("arrayValue") or v.get("kvlistValue")


def attrs(kvs: list) -> dict:
    return {kv["key"]: value(kv["value"]) for kv in kvs or []}


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as f:
        lines = f.read().splitlines()
    lo = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    hi = int(sys.argv[3]) if len(sys.argv) > 3 else len(lines)
    keys: dict[str, set] = defaultdict(set)
    shown: dict[str, Counter] = defaultdict(Counter)
    sums: dict[str, Counter] = defaultdict(Counter)
    resource_keys: set = set()
    paths: Counter = Counter()
    for line in lines[lo - 1 : hi]:
        rec = json.loads(line)
        paths[rec["path"]] += 1
        p = rec["payload"]
        for rl in p.get("resourceLogs", []):
            resource_keys |= set(attrs(rl.get("resource", {}).get("attributes")))
            for sl in rl.get("scopeLogs", []):
                for lr in sl.get("logRecords", []):
                    a = attrs(lr.get("attributes"))
                    name = "event:" + str(a.get("event.name", value(lr.get("body", {})) if lr.get("body") else "?"))
                    keys[name] |= set(a)
                    for k in SHOW:
                        if k in a:
                            shown[name][f"{k}={a[k]}"] += 1
                    for k in NUMERIC:
                        if k in a:
                            sums[name][k] += float(a[k])
        for rm in p.get("resourceMetrics", []):
            resource_keys |= set(attrs(rm.get("resource", {}).get("attributes")))
            for sm in rm.get("scopeMetrics", []):
                for m in sm.get("metrics", []):
                    name = "metric:" + m["name"]
                    data = next((m[k] for k in ("sum", "gauge", "histogram") if k in m), {})
                    for dp in data.get("dataPoints", []):
                        a = attrs(dp.get("attributes"))
                        keys[name] |= set(a)
                        label = ",".join(f"{k}={a[k]}" for k in SHOW if k in a)
                        sums[name][label] += float(dp.get("asDouble", dp.get("asInt", 0)))
    print("export paths:", dict(paths))
    print("resource attribute keys:", sorted(resource_keys))
    for name in sorted(keys):
        print(f"\n{name}\n  keys: {sorted(keys[name])}")
        if shown[name]:
            print("  values:", dict(shown[name]))
        if sums[name]:
            print("  sums:", {k: round(v, 6) for k, v in sums[name].items()})


if __name__ == "__main__":
    main()
