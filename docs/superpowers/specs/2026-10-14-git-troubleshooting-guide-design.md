# Git Troubleshooting Guide Design

**Date:** 2026-10-14  
**Status:** Approved

## Goal

Create a Chinese, symptom-first Git troubleshooting guide that a developer can
search while an error is on screen. The guide must explain Git's state instead
of presenting commands as incantations.

The deliverable is `docs/GIT_TROUBLESHOOTING.md`.

## Audience and scope

The guide targets developers who use branches and remotes daily but do not
always know how the worktree, index, local history, and remote-tracking history
interact.

It covers about 20 frequent situations in five groups:

1. Worktree and index: modified files, staging mistakes, discarded work,
   untracked files, and stashes.
2. Commits and undoing: amend, revert, reset, restoring files, and recovering
   commits.
3. Branches and remotes: non-fast-forward pushes, diverged branches, rebasing,
   upstream configuration, and detached HEAD.
4. Conflicts and collaboration: merge/rebase conflicts, interrupted operations,
   cherry-picks, and concurrent work on a shared branch.
5. Recovery and safety: reflog, deleted branches, destructive-command
   guardrails, large files, and accidentally committed secrets.

The first scenario reproduces the motivating incident:

- `git push` is rejected as non-fast-forward.
- `git pull --rebase` is rejected because tracked changes are unstaged.
- The safe path uses `--autostash` or an explicit tracked-file stash, rebases
  the local commit, restores the worktree, verifies the graph, and then pushes.

## Information architecture

The guide starts with a compact mental model and a symptom-to-section decision
tree. Each troubleshooting section then uses this fixed shape:

1. **Terminal symptom** — exact or representative output.
2. **What the graph means** — a Mermaid diagram showing the relevant state.
3. **Why it happened** — the smallest accurate explanation.
4. **Safe procedure** — commands with prerequisites and expected effects.
5. **Verify** — commands that prove the repository reached the intended state.
6. **Avoid** — destructive shortcuts and when they would lose work.

Sections are organized by symptom rather than by command so a terminal error can
be searched verbatim.

## Visual contract

Every troubleshooting scenario must contain at least one diagram.

- Use Mermaid `gitGraph` when commit ancestry is the key fact.
- Use Mermaid `flowchart` when the reader must choose an action based on state.
- Use layered flowcharts for worktree → index → local commit → remote movement.
- Label local and remote refs explicitly.
- Show destructive edges in words such as "discard" or "rewrite"; color alone
  must never carry meaning.
- Keep each diagram focused on one problem and renderable by GitHub Markdown.

The introductory mental model and decision tree are additional diagrams and do
not replace the per-scenario requirement.

## Safety rules

- Preserve uncommitted and untracked work unless the reader explicitly chooses
  to delete it.
- Prefer `restore`, `revert`, `stash`, and ordinary rebase/merge procedures.
- Show `reset --hard`, `clean -fd`, and force-push only inside clearly marked
  recovery or collaboration constraints.
- Use `--force-with-lease` when a history rewrite is genuinely required; never
  recommend plain `--force`.
- Explain whether a command changes the worktree, index, local history, remote
  history, or some combination.
- Include a checkpoint (`status`, `diff`, `log`, or `reflog`) before any
  irreversible step.

## Acceptance criteria

- `docs/GIT_TROUBLESHOOTING.md` exists and is written in Chinese.
- It contains approximately 20 practical scenarios across all five groups.
- Every scenario has at least one Mermaid diagram.
- Every scenario states prerequisites, commands, and verification steps.
- The non-fast-forward plus dirty-worktree incident is fully covered.
- Destructive commands are never the default path.
- Mermaid fences are balanced and each diagram has a distinct, accurate title
  in the surrounding section.
- The guide does not include repository-specific secrets, remotes, or personal
  paths.

