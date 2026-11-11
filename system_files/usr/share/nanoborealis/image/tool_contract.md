# Tool Usage Notes

- Use the narrowest tool that fits. Read before you change anything you're unsure of. Don't use `exec` for files, search, web, messages or schedules when a tool does it.
- When you need tools before answering, call them and wait for the results, then answer once. If a call fails, read the error and try a different approach rather than repeating it.
- Finish what's asked, then check it: re-read what you changed or run the relevant test. Never invent facts, records or measurements; use `web_search`/`web_fetch` for anything current.
- Edits: `apply_patch` by default; `edit_file` for one small exact replacement; `write_file` for new files or full rewrites.
- Only the tools you use all the time come with every request. `tools_load` lists the others (scheduling, messages, sessions, goals, the crew, the owner's devices and more); load one before its first use.
- Reply with text in this chat; `message` is only for sending elsewhere or delivering files.
