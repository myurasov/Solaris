---
name: kaggle-kernels
triggers: ["kaggle kernel", "kaggle notebook", "kernel push", "push the kernel", "kernel-metadata", "faithful fork", "fork a public notebook", "notebook submission", "commit run", "offline wheels", "build a submission"]
summary: Kernel engineering for Kaggle code submissions, read before building one - faithful forks, private datasets and offline inputs, clean packages, runtime and memory budgets, replays and commit-run checks, kernel-metadata.json, and submitting a saved version.
---
_Rev. 1_

# Skill: kaggle-kernels - Kernel Engineering <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Forks of Public Notebooks](#forks-of-public-notebooks)
- [Datasets and Offline Inputs](#datasets-and-offline-inputs)
- [Building and Packaging](#building-and-packaging)
- [Runtime and Memory Budgets](#runtime-and-memory-budgets)
- [Checking a Kernel Before It Ships](#checking-a-kernel-before-it-ships)
- [Pushes, Versions and Submission](#pushes-versions-and-submission)

## When to Use

Before building, forking, checking or pushing a kernel (a Kaggle notebook) or another package a competition runs,
and before each notebook submission. CLI commands and their 2.2.4 quirks are in the `kaggle-cli` skill, the checks
around a submit in `kaggle-checks`; pushes, dataset uploads and submits are writes under the kaggle rule.

## Forks of Public Notebooks

Which public notebook to build on, and moving pending changes onto a stronger one, is in the `how-to-kaggle` skill.

- **Fork faithfully:** keep every original cell, pin the original's Docker image and machine shape, detach unused
  datasets, credit the authors in a header cell, and expect the commit run's output to equal the author's own, row
  for row. Take the image from a version that actually ran (its run log, or a byte-identical copy's run), not from
  `kernels pull -m` (the latest version's: the `kaggle-cli` skill): a version saved without a run records the CPU
  image even for a GPU notebook, so a GPU step can silently fall back to the CPU and into its time limits. The device
  is part of the recipe: read the torch build and device from the scored run's log, and match both.
- **Pull one version** with `kernels pull <owner>/<kernel>/<version> -m` (CLI 2.2.4 takes the version in the ref).
  Take the version number from the notebook's version list in the browser, read-only (neither the CLI nor the SDK
  lists versions), and check that the pulled code is that version's.
- **Rebuild a public notebook's private inputs:** arrays it reads from someone's private dataset can often be rebuilt
  from public sources (per-candidate counts from a public database, say), aligned row for row with its own tables.
  Check the alignment at the notebook's own indexing, diff your run against the author's visible commit output (it
  fingerprints the private inputs on the rows that use them), and record the upstream files' checksums: the
  alignment holds only for those files. A fork whose private inputs were replaced with public or rebuilt ones is no
  longer faithful, even with every cell unchanged: its score is unknown until the fork itself is scored (one matched
  the author's commit output on every visible row, none of which exercised the replaced arrays). Read its own score
  before stacking variants on it, and keep a fallback build on another source of those inputs.

## Datasets and Offline Inputs

- **Private datasets:** create them before the kernel push and wait for "ready" (subtitle 20-80 characters). Mount
  paths vary (`/kaggle/input/datasets/<owner>/<slug>/`), so resolve your own datasets' folders first and look files
  up only inside them: a recursive search of all of `/kaggle/input` picks up same-named files from other attached
  datasets (with two kernels' inputs attached, a lookup took the other's file and a wheel glob found two wheels).
- **Never publish a new version of a dataset that a pushed or submitted kernel attaches;** give new assets a new
  dataset. `dataset_sources` names bare slugs, so every later push mounts the latest version: a re-push or a
  sibling kernel would run on files nobody reviewed, while the submitted version keeps the old ones.
- **Dataset uploads:** `datasets create|version` skip subfolders by default, with one easy-to-miss line (none under
  `-q`): pass `--dir-mode zip`. Collaborators listed in `dataset-metadata.json` are ignored on create. Before
  `datasets metadata --update`, check the file holds `"isPrivate": true` and every field: CLI 2.2.4 sends
  `isPrivate` false and blanks for missing keys.
- **Kaggle decompresses `.gz` files in datasets,** even inside a zip. Ship plain files, list in the manifest the
  names the kernel will actually see, and check `datasets files` after every upload: a checksum check keyed on the
  `.gz` names would silently fall back and waste a slot.
- **Offline wheels** break when the image's Python changes: keep `docker_image` pinned (the `kaggle-cli` skill).

## Building and Packaging

- **Coupled fallbacks:** every new input or step falls back, all-or-nothing, to the last evaluated configuration,
  with a logged marker; worker pools use timeouts (`map_async(...).get(timeout)`) so a crash cannot hang the run.
- **Build every package from a clean export:** copy only what the package needs into a fresh folder, with no
  bytecode (`__pycache__`, `.pyc`; set `PYTHONDONTWRITEBYTECODE=1` for checks run on it), no `*.sync-conflict-*`
  copies and no notes: stray `.pyc` files failed a host's file-type check, and every file in a scripts folder is
  loaded on each call.
- **Gate every package on the hosts' own validator at the pinned version:** run their packaging or compile step on
  the exact bytes you ship, and again after every scorer update: a package that fails it can score zero with no
  error shown.
- **Evaluate the exact shipped bytes;** after any rebase, re-smoke.

## Runtime and Memory Budgets

- **Hardware banner** at the start of every kernel (CPU count, RAM, GPU) to learn the real environment.
- Time the hidden run from full-run timings, never from a smoke commit run (Smoke-only commit runs, below).
- A GPU commit run queued for hours while its CPU twin finished within the hour.

## Checking a Kernel Before It Ships

- **Replay every candidate kernel in Kaggle's exact image on an x86 host before pushing** (disabling numpy's AVX-512
  kernels, for example, can make CPU output match Kaggle's row for row). It takes minutes, catches silent
  fallbacks, and gives exact outputs for ensembles. Recipe: `kernels pull <ref> -m`; stage every input its
  `kernel-metadata.json` declares (dataset, competition, kernel and model sources) under `input/` at the paths the
  code reads (grep it for `/kaggle/input`); run it on a remote x86 host in Kaggle's pinned image with `input/` and
  `working/` mounted as `/kaggle/input` and `/kaggle/working`; list the inputs left unmapped or private in a status
  table in the workspace README.
- **Mirror the grader exactly in local runs:** its command line, environment and config keys, read from the
  grader's own code. A host's convenience runner ignored the package's config file and skipped a step the scorer
  runs, and a local test timeout followed a package setting that the grader's own test step ignores.
- **Smoke-only commit runs:** some public notebooks run a smoke subset on the commit run (a few rows whenever the
  test is the visible one) and the full set only on the hidden rerun. Verify such a fork on the smoke rows and
  markers only, and plan the hidden run's time from full-run timings (the author's logs, or those of the notebooks
  it combines), never from the commit run. Where the project's own submit check requires one notebook family's
  smoke marker, make it accept a hand-verified check line for a fork of another family, recorded with how it was
  verified (against the author's commit output, say), instead of blocking the fork or being bypassed.
- **Say what a commit run cannot show:** a change whose gate never opens on the visible test (it acts only on cases
  the visible test lacks) can be checked there only for loading: its inputs found, its markers printed, rows equal
  to the base's. Record it as checked for loading only, and let the board read its effect. Likewise, a smoke whose
  stage budget runs out proves nothing about that stage: one stage spent its whole smoke time budget before
  processing a single item, so the smoke rows matched the base's whatever the change did. Read each stage's
  processed count in the log, and check a change to such a stage by a diff of the built kernel and its own log line.
- **Freeze smoke inputs:** never rewrite staged datasets or notebooks while a smoke runs, and hash every input at
  launch and again at the end (`find -L <roots> -type f | sha256sum`); a smoke whose inputs changed verifies nothing.
- **Decode `kernels logs` before matching markers:** it returns JSON records whose text escapes quotes, so a raw
  grep for a marker that contains quotes reports it missing. Match the line that names the whole configured state,
  never a bare `ON` token that a fallback state can print too.

## Pushes, Versions and Submission

- **Check `kernel-metadata.json` before each push:** `id`, a `code_file` that exists, `is_private` true,
  `enable_internet` as the rules allow (a missing key means on), the accelerator and machine (`enable_gpu`,
  `machine_shape`), the image pin (`docker_image`), and only the sources the code reads.
- A kernel slug that equals a dataset slug fails to push (409 Conflict); keep the two names distinct.
- A two-ref push loop failed transiently; push one ref per command.
- **A notebook submission names a saved version:** N is the one `kernels push` printed, and `-f` names the kernel's
  output file; the command and its gate are in the `kaggle-cli` skill.
