# First-party coding-agent repairs

This directory contains a small, public, reproducible evaluation for this repository's `AgentLoop`. The 20 tasks are first-party synthetic Python repairs. They are not copied from open-source issues and should not be presented as a real-world benchmark. The frozen split is 12 `dev` tasks and 8 `heldout` tasks; because the benchmark is public, `heldout` means a fixed reporting partition, not secret data.

Use Python 3.10 or newer; the project verification target is Python 3.12.

Run the deterministic harness demonstration without a model or network call:

```bash
python3 -m evals.runner \
  --split all \
  --run-name offline-demo \
  --output evals/results/offline-demo
```

This replays the checked-in oracle source. It proves that each initial repository fails its public test and that repository creation, frozen tests, acceptance tests, hashing, JSONL output, and Markdown aggregation work. Its pass rate is a harness acceptance rate, never an Agent score. Model completion, latency, tool calls, and token usage remain pending.

Live evaluation requires the explicit `--live` flag. The runner reads the process environment directly and never loads or prints `.env` files:

```bash
ANTHROPIC_API_KEY=... MODEL=... \
python3 -m evals.runner --live --profile baseline --split all \
  --output evals/results/live-baseline

ANTHROPIC_API_KEY=... MODEL=... \
python3 -m evals.runner --live --profile retrieval --split all \
  --output evals/results/live-retrieval

ANTHROPIC_API_KEY=... MODEL=... \
python3 -m evals.runner --live --profile repair --split all \
  --output evals/results/live-repair
```

`ANTHROPIC_AUTH_TOKEN` and `MODEL_ID` are accepted for compatibility with the application. `ANTHROPIC_MODEL` is also accepted. `ANTHROPIC_BASE_URL` is optional for the official API and required when the selected client needs a custom endpoint. Keep the model, split, and `--max-*` arguments identical when comparing profiles.

The profiles differ only in controller capabilities:

| profile | tools | automatic continuation | controller repair |
|---|---|---|---|
| `baseline` | read, write, edit, shell | off | off |
| `retrieval` | baseline plus `lookup_symbol` | off | off |
| `repair` | retrieval tools | off | at most `--max-repair-attempts`, using the public test command |

Every task starts in a new temporary Git repository. After `AgentLoop.run(...)` returns, the evaluator creates frozen public and acceptance scripts outside that repository and runs them with isolated Python startup. A score requires the original public test files to be byte-for-byte unchanged and both external test suites to pass. The task, scoring, and whole-manifest SHA-256 hashes make changes visible. Each live task row in `results.jsonl` records completion, total and Agent tool calls, actual API token usage when provided, latency, repair attempts, human intervention count, errors, test evidence, model, endpoint without credentials, available tools, and budget. `summary.md` aggregates the run without inventing unavailable metrics.

This is process isolation, not a security sandbox. The working directory and Git repository are not security boundaries, and the shell tool can access anything allowed to the runner process. Acceptance files are created only after the Agent stops and scoring uses the manifest already loaded in evaluator memory, which prevents ordinary test editing from earning credit. A hostile Agent may still inspect the host or attack the evaluator. Run live evaluations in a disposable, credential-minimal container or VM with network policy appropriate for the model endpoint.

The manifest is public, so results can also be affected by benchmark memorization. Report the manifest hash, model identifier, endpoint class, profile, budgets, split, and raw output directory with any score.

## Local repository tasks

The synthetic repairs above are smoke coverage. Use `repo_manifest.example.json` to evaluate a real, locally available Git repository at a fixed commit. Each task declares the local repository, full 40-character base commit, issue text, public test command, protected public-test paths, and an executable acceptance script stored outside the repository. An optional oracle patch supports an offline harness check and is never an Agent result.

```bash
python3 -m evals.runner \
  --repo-manifest /path/to/repo_tasks.json \
  --output evals/results/repo-offline-check

ANTHROPIC_API_KEY=... MODEL=... \
python3 -m evals.runner --live --profile baseline \
  --repo-manifest /path/to/repo_tasks.json \
  --output evals/results/repo-live-baseline
```

The runner reads the acceptance script and oracle patch into evaluator memory, clones the exact base commit with `--no-hardlinks`, and never edits the declared source repository. Untracked local files are not cloned. A base commit containing `.env` or `.env.*` other than `.env.example` is rejected. Before any repository test or Agent shell runs, live API credentials are removed from the inherited subprocess environment. The external acceptance executable receives the temporary clone path as its first argument. Set `require_public_failure` when the public command must demonstrate the bug before repair.

No real-repository model run is checked in. Real-repository model results remain pending.

## Recorded local-model smoke

[free-local-smoke/comparison.md](results/free-local-smoke/comparison.md) records two synthetic tasks with the existing free local `qwen2.5:3b` model, accessed over an SSH-forwarded Ollama endpoint. Baseline, retrieval and repair each accepted 0/2 patches. This small result provides a failure baseline, not evidence of improved completion or real-repository ability. The raw logs, model digest, budgets and evaluated source hashes are retained.

The observed failures motivated bounded initial file context and richer edit-mismatch feedback. Development-only follow-up runs are separate from that frozen comparison and do not count as held-out validation. SDK connection setup/stream closure can exceed the nominal wall-clock budget; affected rows are explicitly identified rather than presented as equal-budget results.

To use an already running local Ollama service without a paid API key, set `ANTHROPIC_API_KEY=ollama`, `MODEL=<installed-model>`, and `ANTHROPIC_BASE_URL=http://127.0.0.1:11434` (or an SSH-forwarded port). The key is a placeholder for the [Ollama Anthropic-compatible endpoint](https://docs.ollama.com/api/anthropic-compatibility); it is not a cloud credential.

