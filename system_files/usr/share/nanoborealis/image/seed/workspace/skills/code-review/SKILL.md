---
name: code-review
description: Review a diff or set of files for correctness bugs, reuse, simplification, and efficiency problems. Use when the user asks for a review or audit, or says "check my changes", "review the diff", "any bugs here?", or "look this over".
---

# Code Review

## Steps

1. Get the changes. Run `git diff` and `git diff --staged`. If this is not a git repo, ask which files to look at — do not guess.

2. For each changed region, read enough surrounding code to actually judge it. A diff hunk alone is not enough context to call something a bug.

3. Hunt for defects in this priority order:

   **Correctness — the priority.** Off-by-one errors. Null/undefined dereferences. Inverted conditions and De Morgan mistakes. Unhandled error paths. Race conditions. Resource leaks. Type coercion surprises. Security: injection, path traversal, secrets in source, missing authorization checks.

   **Reuse.** Code reimplementing something that already exists here. Grep to confirm the existing thing is real and actually fits before claiming it.

   **Simplification.** Meaningfully shorter or flatter with no loss of clarity. Real wins only, not style preferences.

   **Efficiency.** Work inside a loop that belongs outside it. Accidental O(n²). Repeated I/O that could be batched. Only flag it if the input size makes it matter.

4. **Filter hard.** For each candidate finding, construct a concrete failure: specific inputs or state, producing a specific wrong result. If you cannot construct one, drop the finding. An unverifiable worry is noise.

5. Sort survivors most severe first.

## Output

For each finding:

```
<n>. <file>:<line> — <correctness|reuse|simplification|efficiency>
     <one sentence stating the defect>
     Fails when: <concrete input or state> -> <concrete wrong result>
```

Close with one line counting findings by category.

If nothing survives step 4, output exactly: `No findings.`

## Do not

- Do not fix anything unless asked.
- Do not restate or summarize the diff.
- Do not comment on formatting or naming unless it causes a bug.
- Do not praise the code.
- Do not list vague "considerations" or "things to keep in mind". Only defects with failure cases.
