# Arm F3r: one repair round after an F3 failure (#196, fixed before any arm run)
Sent after the shared header (common.md) only when the F3 script did not end in FIT_OK; F3 successes carry over.
`<SCRIPT>` is the F3 script, `<RESULT>` the sandbox result (exit status, stdout and stderr tails, 2,000 characters
each). A `NOT_FIT:` reply is the verdict; a script reply runs once and its result is final.

---
You wrote the script below to test whether `<PACKAGE>` does this need. It ran in the sandbox (no network, read-only file system except /tmp, 60-second limit, only `<PACKAGE>` and its dependencies installed) and did not end with FIT_OK.

Script:
```python
<SCRIPT>
```

Result:
<RESULT>

If the failure shows that `<PACKAGE>` cannot do the need in that environment, reply with exactly one line: `NOT_FIT: <reason>`. If it is a mistake in your script, reply with a corrected script only, in one ```python block, under the same rules (at most 40 lines; the capability from `<PACKAGE>`'s public API, not your own code; assertions; FIT_OK last only if every check passed; never catch an exception in order to print FIT_OK). The corrected script runs once more and its result is final.
---
