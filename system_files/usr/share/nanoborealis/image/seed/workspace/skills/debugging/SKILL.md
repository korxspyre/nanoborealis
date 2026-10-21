---
name: debugging
description: Find and fix the root cause of a bug, error, failing test, or unexpected behavior. Use when something errors, crashes, fails, hangs, or behaves differently than expected.
---

# Debugging

## Steps

1. **Reproduce it.** Run the exact failing command or test and capture the full output. If you can't reproduce it, say so and ask for the exact steps. Don't fix a bug you haven't seen.
2. **Read the whole error.** The last line of a traceback usually names the problem; the deepest frame in code you own usually locates it. Note the exact message.
3. **State one hypothesis** about the cause.
4. **Test it with the cheapest experiment:** a print or log line, running one function on its own, checking a value, or reading the code on that path. Don't change code just to see whether it helps.
5. **Fix the root cause, not the symptom.** Don't wrap the failure in try/except, `|| true`, or a retry that hides it.
6. **Verify.** Rerun the original failing command and show it passing. Run the related tests too.
7. **If the hypothesis was wrong,** drop it and go back to step 3. After two wrong hypotheses, step back and re-read the code path from its entry point.

## Output

- **Cause:** one or two sentences
- **Fix:** the files and what changed
- **Proof:** the command you ran and its passing output

## Do not

- Do not make several speculative changes at once.
- Do not claim it's fixed without rerunning the failing case.
- Do not delete, skip, or weaken failing tests to make them pass.
