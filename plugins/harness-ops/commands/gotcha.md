---
description: Capture a one-line lesson about a path into the .claude/rules file scoped to it, with (#issue, date) provenance
argument-hint: <path> <lesson>
---
Capture this lesson: $ARGUMENTS

1. Name the file or directory it is about (it must exist) and write the lesson as one line of at most 25 words:
   what to do, then why. Put repository paths in backticks; every one must exist.
2. Could a test, hook or check catch the mistake (a command, a path, a pattern in code)? Then a rule is the wrong
   tool, since rules are advice that loads only when a matching file is read. Run
   `uv run dev gotcha <path> "<lesson>" --checkable` and file the issue it prints. Stop there.
3. Otherwise run `uv run dev gotcha <path> "<lesson>"`. It appends `- <lesson> (#<issue>, <date>)` to the rules file
   whose `paths:` match, or creates one scoped to the top-level directory, and skips a lesson already there. The
   issue comes from the branch name; pass `--issue N` on another branch.
4. Commit the rules file with the current change. The gate fails any rule line without `(#n, YYYY-MM-DD)`, one that
   names a missing path, and a `paths:` glob that matches no file.
