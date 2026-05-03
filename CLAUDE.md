# Editor preference

Always use `vi` (or `vim`) for any file edit instructions. Never suggest `nano`.

# Multi-machine instructions

When a task touches more than one machine (e.g., desktop + Sparky):

1. **Run everything from one machine when possible.** Prefer one-shot remote
   commands (`ssh host 'cmd'`, `ssh host 'cat >> file' < local_input`) over
   "do this on A, then do that on B". The user should not have to copy a
   block, switch terminals, paste, and switch back.

2. **When you genuinely must split work across machines, separate it
   visually and label every block.** Use a clear header per machine
   (e.g., `**On the desktop:**`, `**On Sparky:**`) and never interleave
   commands for different machines inside the same fenced block.
