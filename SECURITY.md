# Security policy

## Reporting a vulnerability
Report it privately, never in a public issue, pull request or discussion: on the repository's GitHub page, open the
**Security and quality** tab and click **Report a vulnerability**
([GitHub's steps](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)).
Say what is affected, how to reproduce it and what an attacker gains. If that button is missing, open an issue that
asks the maintainers for a security contact, with no details of the vulnerability.

Leaked secrets count: a key, token or personal data in a commit, a transcript or a log. Report them the same way.

## Supported versions
Fixes go to the default branch and the newest release tag. Older releases are not patched; update instead.

## Scope
The agent tooling (the `uv run dev` command, the `harness-ops` Claude Code plugin and the Copier template) comes
from [agent-harness](https://github.com/Marius-Juston/agent-harness). A vulnerability in it belongs there:
[report it to agent-harness](https://github.com/Marius-Juston/agent-harness/security/advisories/new).

## For maintainers
Private reporting is a repository setting: Settings, then Advanced Security, then enable Private vulnerability
reporting
([GitHub's steps](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository)).
