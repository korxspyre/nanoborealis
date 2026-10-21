---
name: simplify
description: Apply a cleanup pass to changed code — remove dead weight, inline premature abstractions, consolidate duplication. Use when the user says "clean this up", "make this simpler", "reduce the noise here", or wants tidying after a feature lands. This is a quality pass, not a bug hunt.
---

# Simplify

Quality only. For bugs, use the `code-review` skill instead.

## Steps

1. Get the scope. Default to `git diff` plus `git diff --staged`. If the user named files, use those.

2. Look for these, in order:

   **Reuse.** Code duplicating something already in the repo. Grep to confirm the existing thing is real and actually fits before claiming it.

   **Dead weight.** Unused variables, imports, parameters. Unreachable branches. Error handling for states that cannot occur. Backwards-compat shims for code with one caller.

   **Premature abstraction.** A helper with one call site. A config option nobody sets. An interface with one implementation. Inline it.

   **Altitude.** Manual loops where the language has a builtin. Hand-rolled parsing where an existing dependency already does it.

   **Comments explaining WHAT.** Delete them. Keep only comments explaining a non-obvious WHY — a hidden constraint, a workaround for a specific bug, genuinely surprising behavior.

3. For each change, be able to state what concretely improves. "Fewer lines" is not a reason if clarity drops.

4. Make the edits. Unlike review, this skill applies changes.

5. **Verify.** Read each edited file back and confirm the change actually landed. Run the tests if there are any. Report what you verified, not what you intended.

## Output

One line per change:

```
<file>:<line> — <what you did> (<why>)
```

Then the test result, or `no tests found`.

## Do not

- Do not rename things for taste. Renames churn diffs and break muscle memory.
- Do not reformat code you aren't otherwise changing.
- Do not "improve" error messages, logging, or docs unless asked.
- Do not add anything. This skill only removes and consolidates.
- Do not touch files outside the scope from step 1.
