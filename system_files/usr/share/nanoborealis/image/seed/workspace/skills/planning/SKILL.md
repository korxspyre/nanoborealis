---
name: planning
description: Read the relevant code and produce a step-by-step implementation plan without writing code. Use when the user asks how to approach something before building it — "how would you tackle X", "plan this out first", "what's the right architecture here", "think through this before coding".
---

# Planning

## Steps

1. **Restate the goal** in one sentence, in your own words. If the restatement would be a guess, put the ambiguity in Blocking questions rather than inventing a requirement.

2. **Read the code first.** Grep for the symbols involved. Read the files this will touch. A plan written without looking at the codebase is a guess dressed up as a plan. This step is not optional.

3. **Build the step list.** Every step states:
   - The specific file and function that changes — not "update the backend"
   - What it unblocks
   - What could break, if anything

   Order steps so the codebase works after each one. No step may depend on a later step.

4. **Name the 2-4 critical files** — the ones holding load-bearing logic changes, where bugs will hide.

5. **State the tradeoff.** What was the alternative approach, and why is it worse here? If there was genuinely no fork in the road, say so rather than manufacturing one.

6. **Stop.** Do not write code. Present the plan and wait.

## Output

```
Goal: <one sentence>

Steps:
1. <file>:<function> — <what changes> — <why>
2. ...

Critical files:
- <path> — <what makes it load-bearing>

Tradeoff: <chosen> over <alternative>, because <reason>
          (or "None — this is the only sensible approach.")

Blocking questions:
- <anything that must be answered first, or "none">
```

## Limits

- Maximum 8 steps. If the task needs more, say it should be split and propose the split.
- Do not include "write tests" or "review the code" as steps — those are assumed.

## Do not

- Do not write implementation code, or snippets longer than a function signature.
- Do not hedge every step with caveats. Commit to an approach.
