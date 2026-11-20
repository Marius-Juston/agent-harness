# Research: measuring the harness: Claude Code OpenTelemetry, transcripts and PR data into tokens and rework per merged issue

Status: v1 (2026-11-19). Issue: #502. Serves #534 (`dev metrics`), #535 (history store) and #536 (OTLP sink).
Labels: SOURCED = cited and fetched; VERIFIED = measured here (script named; transcripts and telemetry are the owner's, so the numbers reproduce only on the owner's machine and only while Claude Code keeps the transcripts); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit search` on 2026-11-19 printed `discovery 2026-11-19: OK, partial (s2 down; openalex, crossref answered)`, so Semantic Scholar-only records were missed. The question is mostly about one tool's signals, so the primary sources are Claude Code's own documentation (fetched as markdown on 2026-11-19) and runs of Claude Code 2.1.328 on this machine.
Companions: [harness-efficiency.md](harness-efficiency.md) (§2 cost model, §4 review rounds; this note measures what its §9 asks for), [issue-pr-automation.md](issue-pr-automation.md) (the fix loop). Scripts: [_scratch/harness-metrics/scripts/](_scratch/harness-metrics/scripts/otlp_sink.py).

## 0. Findings in one paragraph
Claude Code 2.1.328 exports, over OTLP to a local endpoint, per-request tokens by kind, `cost_usd`, model, effort, `query_source`, the subagent's `agent_id` and its agent name, plus a `subagent_completed` event with run time and tool count. On a probe run these numbers equal the transcripts' own usage on 12 of 12 requests (VERIFIED, §2). So **telemetry adds little that the transcripts lack today**. Transcripts already carry model and effort on every assistant line (5,418 of 5,418 in this repository's last 30 days). They also already give the agent type and the issue, through the run description. Telemetry has three catches. The harness's plugin agents arrive as `"custom"` unless `OTEL_LOG_TOOL_DETAILS=1`, and that flag also exports every Bash command. Resource attributes are per process, so subagents inherit the lead's. `user.email` is attached whenever the user is signed in. The real gaps are elsewhere. **Transcripts are deleted after 30 days by default**: this repository's oldest is exactly 30 days old. Today's per-issue figure divides by merged issues that have no transcript (45 of 76 covered). And no report counts send-backs, model or wall time. Baseline for 2026-10-20..11-18: 77 merged PRs; 310M tokens and $138.6 transcript-priced, 97.6 % of the tokens cache reads, every turn on Opus 5.5. Per covered issue the median is 2.0M tokens and $1.32 (IQR 1.3-4.5M, $0.86-2.43). 4 of 77 PRs were sent back by integrate for a failure (5 %, CI 2-13 %) and 7 of 77 counting conflicts. 10 of 13 reviewer series passed within one review-and-fix round, but only 13 of 77 PRs have a reviewer series in the retained transcripts. Build `dev metrics` on `devkit/stats.py` (#534), keep a content-free run history (#535), and treat a local OTLP sink as optional (#536).

## 1. Question, scope and data
The owner's question: which numbers make "tokens per merged issue" and "rework" (review rounds, send-backs) measurable by agent type and model? It splits into four parts:
- what Claude Code's OpenTelemetry signals carry today;
- a local setup that keeps the data private (this repository is public);
- what they add over the transcripts that `dev agent-stats` already reads;
- this repository's current baseline.

Data:
- Two `claude -p --model haiku` probe sessions on Claude Code 2.1.328. Each spawned one custom subagent defined with `--agents`. They exported OTLP/HTTP-JSON to [otlp_sink.py](_scratch/harness-metrics/scripts/otlp_sink.py) on 127.0.0.1, 18 export requests in all, summarised by [otlp_summary.py](_scratch/harness-metrics/scripts/otlp_summary.py) and joined to the transcripts by [otel_vs_transcript.py](_scratch/harness-metrics/scripts/otel_vs_transcript.py).
- This repository's transcripts and GitHub data for 2026-10-20..2026-11-18, read through `devkit.stats` and the scripts in §6.
- The window ends the day before the run date. One lead session was still being written during the runs, so its dollars moved by under $1 between scripts (VERIFIED: $137.75 in one run, $138.59 in a later one).

## 2. What Claude Code's OpenTelemetry exports today
- **Switches.** `CLAUDE_CODE_ENABLE_TELEMETRY=1` turns telemetry on. `OTEL_METRICS_EXPORTER` accepts `console`, `otlp`, `prometheus` or `none`, and `OTEL_LOGS_EXPORTER` accepts `console`, `otlp` or `none`. Protocols are `grpc`, `http/json` and `http/protobuf`. Traces are a beta behind `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` (SOURCED, [Monitoring](https://code.claude.com/docs/en/monitoring-usage), "Common configuration variables" and "Traces (beta)"). The documentation lists no file exporter, so a file needs a receiver: a collector, or the 55-line stdlib sink used here (§3).
- **Metrics seen.** The probe produced 4 of the 8 documented metrics: `claude_code.session.count`, `claude_code.cost.usage`, `claude_code.token.usage` and `claude_code.active_time.total`. The other four (lines of code, commits, PRs, edit decisions) need events that did not happen (VERIFIED, otlp_summary.py). Cost and token data points carry `model`, `query_source` (`main` or `subagent`), `effort` and `agent.name`. The token metric splits `type` into `input`, `output`, `cacheRead` and `cacheCreation`. Documented: "The `cacheCreation` type counts cache writes with the five-minute and one-hour cache lifetimes together" (SOURCED, [Monitoring](https://code.claude.com/docs/en/monitoring-usage), "Token counter").
- **Events seen.** The probe produced these log events: `api_request`, `assistant_response`, `user_prompt`, `tool_decision`, `tool_result`, `subagent_completed`, `plugin_loaded` and `managed_settings_resolved` (VERIFIED). Each `api_request` carries:
  - tokens: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, and the cache writes split into `cache_creation_5m_tokens` and `cache_creation_1h_tokens`;
  - cost: `cost_usd` and `cost_usd_micros`;
  - timing: `duration_ms`;
  - identity: `model`, `effort`, `query_source`, `request_id`, `prompt.id`, `session.id` and, for subagents, `agent_id` and `agent.name` (VERIFIED, matching the "API request event" list in [Monitoring](https://code.claude.com/docs/en/monitoring-usage)).

  Three keys appear that the page does not list: `anthropic.plan`, `anthropic.provider` and `anthropic.custom_base_url` (VERIFIED; their values are not recorded here). On the event, `query_source` read `sdk` for the `-p` main thread and `agent:custom` (later `agent:probe`) for the subagent. That is a different vocabulary from the metric's `main`/`subagent` (VERIFIED).
- **Events and metrics agree.** Run 1's five `api_request` events sum to $0.006103, the same as its two cost data points ($0.003388 main + $0.002715 subagent). Its tokens agree kind by kind: 14 input, 552 output, 66,810 cache read and 33,618 cache write (VERIFIED, otlp_summary.py).
- **Telemetry equals the transcripts on this version.** All 12 `api_request` events joined a transcript request by `request_id`. The transcript's largest logged usage equals the event on every token kind (ratio 1.00 for each of the four). Both subagent `agent_id`s name a `subagents/agent-<id>.jsonl` file (VERIFIED, otel_vs_transcript.py; N = 12 requests on Haiku 5.5, two sessions, so a check of agreement, not of every model or of long outputs). This matches the scorecard over all retained transcripts (it ignores `--since`, #534): "0% of messages logged only a stream-start usage" (VERIFIED, `uv run dev agent-stats`). The 6.4x output undercount of [harness-efficiency.md](harness-efficiency.md) §2 is therefore a property of older builds, not of 2.1.328 (BELIEVED; this repository's `hidden_per_turn` also came out 0 in the window).
- **Subagent completion.** `subagent_completed` carries `agent_type`, `agent.source`, `duration_ms`, `total_tool_uses`, `model`, `final_model` and `model_swapped`. Its `total_tokens` is not a run total: "The token footprint of the subagent's final API request ... Not a sum across the run" (SOURCED, [Monitoring](https://code.claude.com/docs/en/monitoring-usage), "Subagent completed event"; VERIFIED present, 14,342 and 15,088 tokens on the two probes).

## 3. A private local setup
- **Plugin agent names are redacted by default.** Run 1 had no details flag, and its subagent arrived as `agent.name=custom` and `agent_type=custom`. Run 2 set `OTEL_LOG_TOOL_DETAILS=1`, and the same subagent arrived as `probe` (VERIFIED, otlp_summary.py). The documentation says: "Other user-defined agent names are replaced with `"custom"`" (SOURCED, [Monitoring](https://code.claude.com/docs/en/monitoring-usage), "Cost counter", `agent.name`). harness-ops is not an official-marketplace plugin, so a split by agent type through telemetry needs the flag (BELIEVED, from the two sources above).
- **That flag exports commands.** With it on, `tool_result` and `tool_decision` gained `tool_parameters`, and `tool_result` gained `tool_input` (VERIFIED). The documentation: "`tool_result` and `tool_decision` events include a `tool_parameters` attribute with Bash commands ... Fields such as `full_command` are emitted untruncated" (SOURCED, [Monitoring](https://code.claude.com/docs/en/monitoring-usage), "Security and privacy"). A gate or log line can hold a credential, so the receiver must drop these keys at ingest (BELIEVED).
- **Identity is attached unless removed.** "When authenticated via OAuth, `user.email` is included in telemetry attributes, sent only to the OTel endpoint you configure, never to Anthropic" (SOURCED, [Monitoring](https://code.claude.com/docs/en/monitoring-usage), "Security and privacy"). `user.account_uuid` and `user.account_id` go away with `OTEL_METRICS_INCLUDE_ACCOUNT_UUID=false`, but `user.email`, `user.id` and `organization.id` are "Always included" when available (SOURCED, same page, "Standard attributes"). The sink deletes all of them, and the host keys, before writing. The resulting file had 0 occurrences of any of them (VERIFIED, `grep -c` on the probe file).
- **Content is redacted by default.** `user_prompt` carries `prompt` and `prompt_text`, and `assistant_response` carries `response`, even without the content flags. Their values were `<REDACTED>` in 5, 5 and 9 of 5, 5 and 9 events (VERIFIED, otel_vs_transcript.py). Assistant text is "not collected by default" (SOURCED, [Monitoring](https://code.claude.com/docs/en/monitoring-usage), "Security and privacy").
- **Per-process tags do not reach a subagent separately.** `OTEL_RESOURCE_ATTRIBUTES=devkit.issue=502,devkit.role=probe-lead` landed on every metric data point and event. That includes the 4 `api_request` events of the subagent, which therefore carry the lead's tag (VERIFIED, run 2). A lead that dispatches builders for many issues cannot tag them this way. Issue attribution of subagents has to go through `agent_id` → subagent transcript → its description, as `stats._target` does today (BELIEVED).
- **Choice of receiver.**
  - Chosen: the stdlib sink [otlp_sink.py](_scratch/harness-metrics/scripts/otlp_sink.py). It binds 127.0.0.1, accepts OTLP/HTTP-JSON, deletes identity keys, appends JSONL and exits after a deadline. No dependency, no daemon, and a run cannot hang a shell.
  - Alternative: the OpenTelemetry Collector's file exporter, in the core and contrib distributions, Apache-2.0, 5,060 stars, pushed 2026-11-19 (VERIFIED with `gh api`). It is "alpha" for traces, metrics and logs, and its README says "there is no guarantee that exact field names will remain stable" (SOURCED, [File Exporter README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/fileexporter/README.md)). It needs a Go binary or a container, and an `attributes` processor to drop keys.
  - Anthropic's [Claude Code ROI Measurement Guide](https://github.com/anthropics/claude-code-monitoring-guide) is a Docker Compose stack: no license file, last push 2025-07-29 (VERIFIED with `gh api`). It does not fit a private local file.

  The env block that worked: `CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp OTEL_EXPORTER_OTLP_PROTOCOL=http/json OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:<port>`, plus `OTEL_LOG_TOOL_DETAILS=1` for agent names (VERIFIED). The data stays on the machine and goes in the state directory, never in the repository.

## 4. Transcripts versus OpenTelemetry for this harness
- **Transcripts already carry model, effort and the issue text.** In the window, 5,418 of 5,418 assistant lines had an `effort` field (VERIFIED, [branch_attribution.py](_scratch/harness-metrics/scripts/branch_attribution.py)). Researchers and the research-auditor ran at `high`, every other role at `medium`, and every turn used `claude-opus-5-5` (VERIFIED, [by_model.py](_scratch/harness-metrics/scripts/by_model.py)). So a split by agent type, model and effort needs no telemetry.
- **The transcript's `gitBranch` is not an issue key.** 37 builder runs had both a numbered branch on their lines and an issue from the description heuristic. The two disagreed on 17 of them (46 %, CI 31-62 %). In each of the 17, the description ("Build #463+#462 ...") named the issue and the modal `gitBranch` named another in-flight issue (VERIFIED, branch_attribution.py and a listing of the 17). Keep the description heuristic, and keep `#<n>` first in agent descriptions (BELIEVED).
- **Transcripts expire.** The settings reference: "**Default**: `30`" days, and "The sweep deletes transcripts without showing a message" (SOURCED, [Settings reference](https://code.claude.com/docs/en/settings-reference), `cleanupPeriodDays`). The oldest main-checkout transcript of this repository is from 2026-10-20, 30 days before this run (VERIFIED, `ls -t` on the project directory). So [harness-efficiency.md](harness-efficiency.md)'s window (2026-10-11..16) can no longer be recomputed from transcripts. Any before/after comparison of a harness change needs a history store (#535) or a larger `cleanupPeriodDays`.
- **Transcripts are not a contract; telemetry is.** "The transcript entry format is internal to Claude Code and changes between versions, so a pipeline that joins on these fields can break on any release" (SOURCED, single-source, [Monitoring](https://code.claude.com/docs/en/monitoring-usage), "Event correlation attributes"). This is the main reason to keep the OTLP route documented and optional (#536), not to switch to it.
- **What transcripts miss is small here.** Over the 3 sessions with cost records, transcript-priced dollars were $137.75 against the records' $131.16, and the records' own `costUSD` was $129.05 (`uv run dev agent-stats --cost`). Most of the uncached input the transcripts miss (6k against 1.1M tokens) costs cents (VERIFIED). Telemetry would add the requests no transcript logs (compaction, auxiliary calls) with Claude Code's own `cost_usd`. The probe had no such request, so that gain is unmeasured (gap, #536).

## 5. Joining GitHub: review rounds, send-backs, wall time
- **Send-backs.** Integrate posts a `<!-- devkit:fix-loop cycle=N head=... key=... outcome=... -->` marker on each send-back ([devkit/fixloop.py](../../devkit/fixloop.py) `fix_loop`). A conflict caused by another merge is cycle 0, "not counted". Counting the markers on merged PRs gives the send-back rate per PR and per outcome (VERIFIED, [github_baseline.py](_scratch/harness-metrics/scripts/github_baseline.py)).
- **Review rounds** come from the reviewer transcripts (`stats.review_series`), not from GitHub. 71 of 77 merged PRs have no GitHub review at all, because agent reviews come back as hand-backs (VERIFIED, github_baseline.py).
- **Wall time.** PR open → merge is a median 0.2 h (IQR 0.1-0.3), because the worker opens the PR at the end. The first attributed run → merge is a median 0.3 h (IQR 0.2-0.4, n = 45) (VERIFIED, github_baseline.py and by_model.py). The second is the one to report. The first measures only integrate.
- **Per-task cost varies widely, so report distributions.** "Repeated runs of the same AI coding agent on the same task differ widely in what they cost and whether they succeed". That preprint replays 13,681 attempts on 500 SWE-bench Verified tasks and scores cost per task including failures (SOURCED, single-source, Murphy 2026, [Restart Economics for AI Agents: Cutoffs, Retries, and Cost per Resolved Task](https://doi.org/10.2139/ssrn.7538000), SSRN preprint, abstract). This repository's per-issue IQR spans 3.4x in tokens (VERIFIED, §6). A cost model that names a token overhead for "output rejection and retries" (a Revision Factor) exists only as an uncalibrated proposal (SOURCED, El-Ramly 2026, [ACEM: A Cost Estimation Model for Agentic Software Engineering](https://arxiv.org/abs/2608.02582), arXiv, abstract: "constants left symbolic pending empirical grounding").

## 6. Baseline for this repository (2026-10-20..2026-11-18)
All VERIFIED. Snapshot of 2026-11-19, transcripts on the owner's machine, dollars at `[tool.devkit.prices]`.

| measure | value | script |
|---|---|---|
| merged PRs / merged issues | 77 / 76 | github_baseline.py, by_model.py |
| merged issues with a run in the retained transcripts (covered) | 45 of 76 (59 %, CI 48-70 %) | by_model.py |
| tokens, all kinds / transcript-priced $ | 310.2M / $138.59; cache reads 97.6 %, output 0.58 % | [tokens_per_issue.py](_scratch/harness-metrics/scripts/tokens_per_issue.py) |
| per merged issue, naive (all spend / 76) | 4.08M tokens, $1.82 | tokens_per_issue.py |
| per covered issue, attributed runs only | median 2.04M tokens (IQR 1.32-4.48M), $1.32 (IQR 0.86-2.43) | tokens_per_issue.py |
| shared (lead and runs naming no issue) | 135.3M tokens, $47.58 | tokens_per_issue.py |
| $ by agent type (all Opus 5.5) | builder $74.31 (40 runs), lead $43.7 (3 sessions), researcher $8.63, reviewer $5.98, others $5.5 | `dev agent-stats --cost`, by_model.py |
| effort | `high` for researcher and research-auditor, `medium` for all others | branch_attribution.py |
| PRs sent back by integrate for a failure (counted cycle ≥ 1) | 4 of 77 (5.2 %, CI 2.0-12.6 %) | github_baseline.py |
| PRs sent back at all, conflicts included | 7 of 77 (9.1 %, CI 4.5-17.6 %); 10 markers: 5 conflict, 4 refused, 1 gate-failed | github_baseline.py |
| reviewer series passing within one review-and-fix round | 10 of 13 (77 %, CI 50-92 %); code 9 of 12; 0 series of 5+ rounds | `dev agent-stats --reviews` |
| PRs with a reviewer series in the transcripts | 13 of 77 | `dev agent-stats --reviews` |
| hours, first attributed run → merge | median 0.3 (IQR 0.2-0.4, n = 45) | by_model.py |

Read with care. The naive per-issue figure divides by 31 issues whose runs are absent: built before the window, or by sessions whose transcripts are elsewhere or deleted. The covered median leaves out the lead's shared $47.58. Neither number compares with the $15 per merged PR of repo-E in [harness-efficiency.md](harness-efficiency.md) §0: that was another project, older Claude Code builds and a corrected output count (BELIEVED). Wilson intervals: [wilson.py](_scratch/harness-metrics/scripts/wilson.py).

## 7. `dev metrics` design
One command on top of `devkit/stats.py`'s single reader (`load_runs`), so the scorecard, `--cost`, `--reviews` and metrics count alike (BELIEVED; filed as #534):
1. **Rows by (agent type, model, effort):** runs, tokens by kind, $. Model and effort are read per turn from the transcript.
2. **Rows by covered merged issue:** tokens, $, runs by type, review rounds per role (`review_series`), counted send-backs and conflicts (fix-loop markers), and hours from first run to merge. An issue is covered when its first attributed run and its merge both fall in the window.
3. **Summary**, every rate with its n:
   - median and IQR tokens and $ per covered issue, printed as n covered of n merged;
   - shared lead spend per covered issue;
   - **rework rate**: covered merged PRs with a counted send-back or a reviewer series of 2+ rounds;
   - **rework tokens**: tokens a worker spends after its first hand-back, resumed for review findings or a send-back.
4. **History** (#535): content-free per-run rows stored under the state directory, so baselines outlive the 30-day sweep.
5. **Telemetry, optional** (#536): a sink that adds untranscribed requests and Claude Code's `cost_usd`.

Out of scope: the per-model breakdown #287 asked for is part of item 1, so #287 can close when #534 lands (BELIEVED).

## 8. Recommendation for this project
- **Build #534 first.** It needs no new data source. Targets that a harness change should move, from §6:
  - median tokens per covered issue below 2.0M with n ≥ 30;
  - counted send-back rate at or below 5 % (4 of 77);
  - one-round reviewer convergence at or above 77 % with n ≥ 20 series. Today's n of 13 cannot detect less than about a 30-point change (BELIEVED, from the interval width).
- **Set `cleanupPeriodDays` well above 30 on the machines that run the harness** (any value up to the documented example 3650), until #535 stores history. A 30-day default loses every baseline a month after it is taken (SOURCED, [Settings reference](https://code.claude.com/docs/en/settings-reference), `cleanupPeriodDays`; VERIFIED, §4).
- **Do not make OpenTelemetry a dependency.** Use it only as an opt-in documented route (#536). If it is on, use exactly the env block of §3 with `OTEL_LOG_TOOL_DETAILS=1` and the dropping sink, never a remote endpoint. It is identical to the transcripts on the measured fields (12 of 12 requests). It cannot tag subagents by issue. The flag needed for agent names exports full commands (VERIFIED, §2-§3).
- **Keep `#<n>` first in every agent description.** It is the only reliable issue key: `gitBranch` disagrees on 46 % of builder runs (VERIFIED, §4).
- **Raise coverage of review data.** Only 13 of 77 merged PRs have a reviewer transcript in the window, so a review-rounds metric means little until review runs are retained (#535) and attributed (BELIEVED).

## Sources
Measured (VERIFIED), all in [_scratch/harness-metrics/scripts/](_scratch/harness-metrics/scripts/otlp_sink.py): [otlp_sink.py](_scratch/harness-metrics/scripts/otlp_sink.py), [otlp_summary.py](_scratch/harness-metrics/scripts/otlp_summary.py), [otel_vs_transcript.py](_scratch/harness-metrics/scripts/otel_vs_transcript.py), [branch_attribution.py](_scratch/harness-metrics/scripts/branch_attribution.py), [by_model.py](_scratch/harness-metrics/scripts/by_model.py), [tokens_per_issue.py](_scratch/harness-metrics/scripts/tokens_per_issue.py), [github_baseline.py](_scratch/harness-metrics/scripts/github_baseline.py), [wilson.py](_scratch/harness-metrics/scripts/wilson.py), and `uv run dev agent-stats` (scorecard, `--cost`, `--reviews`) for 2026-10-20..2026-11-18. The probe telemetry files are kept off the repository: they carry session ids.

Claude Code documentation (markdown fetched 2026-11-19):
- [Monitoring](https://code.claude.com/docs/en/monitoring-usage): configuration variables, standard attributes, metrics, events, traces, security and privacy.
- [Settings reference](https://code.claude.com/docs/en/settings-reference): `cleanupPeriodDays`.

Software:
- [File Exporter README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/fileexporter/README.md), OpenTelemetry Collector contrib (Apache-2.0).
- [Claude Code ROI Measurement Guide](https://github.com/anthropics/claude-code-monitoring-guide), Anthropic (no license file).

Literature (abstracts fetched with `uv run dev lit get`):
- Murphy, J. E. (2026). [Restart Economics for AI Agents: Cutoffs, Retries, and Cost per Resolved Task](https://doi.org/10.2139/ssrn.7538000). SSRN preprint.
- El-Ramly, M. (2026). [ACEM: A Cost Estimation Model for Agentic Software Engineering](https://arxiv.org/abs/2608.02582). arXiv 2608.02582.

## Follow-ups
- [ ] #534 `dev metrics`: tokens, $, wall time and rework per covered merged issue, by agent type, model and effort DONE WHEN its DONE WHEN boxes are ticked and the PR body shows the §6 baseline reproduced within ±10 %
- [ ] #535 content-free run history that outlives the 30-day transcript sweep DONE WHEN `dev metrics --snapshot` is idempotent and whitelist-tested
- [ ] #536 optional local OTLP sink DONE WHEN the sink drops the identity and content keys of §3 (fixture test) and `dev metrics` reports requests with no transcript per `query_source`
- [ ] Measure telemetry against transcripts on a long Opus 5.5 builder run with compaction DONE WHEN #536's report shows the share of $ in requests no transcript logs, over at least one week of harness work
