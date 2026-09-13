# Panel runs on this repository

This directory is the record of the review panel being run three times: on
`feat/per-tenant-rate-limit` (run 1), on the first fold of that branch (run 2, commit `7a6e499`),
and on the second fold (run 3, commit `84efc3a`), using the public
[multi-persona-review-panel](https://github.com/FortunaTerra-Group/multi-persona-review-panel-skill)
skill and nothing else. Each lens's full output is logged verbatim, findings and recommendations
included, so you can compare your own run against it.

## What produced these logs

- **Model:** Claude Opus 5 (1M context), via Claude Code, on 2026-09-12. Every file under
  `run-1-flawed/` and `run-2-folded/` is that model's output. It is not a human review, and it is
  not the only possible output.
- **Procedure:** the four generic lenses from the skill's `SKILL.md` (Architecture, Security,
  Quality, Performance), each as its own independent agent, each given the lens brief in
  `PROMPTS.md`, the diff (`git diff main...HEAD`), the files it touches, and `CONTRACT.md`. Each
  agent could run the test suite and write probe scripts. None saw the others' output. None saw
  the worked example in the skill repository.
- **Variance:** a rerun with the same model reproduces the findings but not the wording, and
  can differ on severity labels (one run called the fixed window a BLOCK, another called it a
  required change). A different model may find fewer or more items. The three seeded root causes
  in run 1 were found by all four lenses in both of our runs of it; that is the part you should
  expect to reproduce.
- **Edits to the logs:** absolute filesystem paths were made repository-relative, one
  scratch-directory path was removed, and em dashes were replaced with plain punctuation.
  Two editor's notes are marked as such. Nothing else was changed. Where a lens says it read this
  repository's README, note the README at that time listed the seeded defects; it no longer does,
  so a fresh run is a fair test.

## Reproduce it

```bash
git clone https://github.com/FortunaTerra-Group/review-panel-demo && cd review-panel-demo
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
git checkout feat/per-tenant-rate-limit
python -m pytest -q                                  # green: that is the point
claude plugin marketplace add FortunaTerra-Group/claude-plugins
claude plugin install multi-persona-review-panel@fortunaterra
claude
# in the session:  /multi-persona-review-panel   (scope: git diff main...HEAD; contract: CONTRACT.md)
```

Then compare your four verdicts and the fold list against `run-1-flawed/synthesis.md`. To see what
the later runs looked like, check out `feat/per-tenant-rate-limit-folded` and run it again; the
branch tip is one fold past run 3, so expect fewer findings than `run-3-folded/`, not zero. Each
`synthesis.md` ends with what was folded and what was left as a note.

If your agent runtime cannot spawn parallel sub-agents, run the four briefs in `PROMPTS.md` as four
separate sessions and synthesize by hand. The independence is the part that matters, not the
tooling.
