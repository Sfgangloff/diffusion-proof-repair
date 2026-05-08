# Project plan — Diffusion / EBM Proof Repair on Lean 4

Owner: silvere · Compute: 1× Colab T4, optional Colab Pro A100 ·
Companion docs: `project_description.md`, `bibliography.md`.

This plan sequences the work into nine phases. Each phase has a single
**goal**, a **done-when** criterion, and a **risk register**. Phases
are ordered so that the harness is end-to-end usable as early as
possible (after Phase 2), then we layer model families and ablations
on top.

## Phase map

| # | Phase | Done-when | T4 fits? |
|---|-------|-----------|----------|
| 0 | Infrastructure & Lean harness — **DONE (2026-05-08, `160e386`)** | One CLI command corrupts and verifies a Lean file end-to-end | yes |
| 1 | Benchmark curation | 50 hand-checked Lean files in a fixed manifest, with annotated lemma/tactic spans | yes (CPU) |
| 2 | Corruption taxonomy & typed operators | 5 corruption operators × 2 localities, parametrised by `ρ`, all property-tested | yes |
| 3 | Small-AR pilot (end-to-end) | `success_rate(ρ)` curve for a single AR model on the 15-file pilot subset | yes |
| 4 | Diffusion track | Same curve for 2 small diffusion checkpoints, with hosted-API anchor at one `ρ` | yes / API |
| 5 | EBM track | Same curve for residual `E_θ` + verifier-as-hard-constraint sampler | yes |
| 6 | Full ρ-sweep | 50 files × 6 `ρ` × 5 corr-types × 2 localities × ≥3 models, plotted | partial / Pro |
| 7 | Verifier-in-the-loop ablation | Quantified Δρ* from giving each model access to `E_lean` during sampling | yes |
| 8 | MCP tool prototype | `repair_proof` and `autoformalize_chunk` callable from Claude Code, with integration test | yes |

The full plan ships when phase 8 lands; phases 0–6 are the **research
artifact**, phases 7–8 are the **product artifact**. A writeup is
written incrementally (one section per phase), not as a final phase.

---

## Phase 0 — Infrastructure & Lean harness — DONE

**Status.** Landed 2026-05-08 in commit `160e386`. `compile`, `verify`,
process-pool variant, SQLite cache, CLI, three Lean fixtures, 15
passing pytest cases, Colab bootstrap notebook, and GitHub Actions CI
are all in place. `corrupt` and `sweep` ship as stubs returning exit
code 2 — they're owned by Phases 2 and 6.

**Goal.** A reproducible Python project that can take any compiling
Lean 4 file, run it through a corruption + verify cycle, and return a
structured result.

**Work items.**

- Repo skeleton: `pyproject.toml`, `src/proofrepair/`, `tests/`,
  `data/`, `experiments/`, `notebooks/`.
- Pin Lean 4 toolchain via `lean-toolchain` and `lakefile.lean`;
  vendor a minimal Mathlib4 import set to keep elaboration fast.
- `proofrepair.lean.compile(path, timeout) -> CompileResult` — wraps
  `lake env lean` (or the Lean LSP) and returns
  `{ok: bool, errors: list[Error], compile_time_s: float}`. Errors
  carry line/column, severity, and message.
- `proofrepair.lean.compile_in_pool(paths)` — `concurrent.futures`
  process pool, default `min(n_files, n_cpus - 1)` workers.
- Hash-keyed cache (`shelve` or sqlite) keyed on
  `(file_sha, lean_toolchain_sha)` so reruns hit memory.
- `proofrepair.cli` with subcommands `compile`, `corrupt`, `verify`,
  `sweep`. Exposed via `python -m proofrepair`.
- Test fixtures: 3 trivial Lean files (one passes, one has a tactic
  error, one has a parse error). Tests assert all three classify
  correctly.
- Colab bootstrap notebook: installs elan, sets up the toolchain,
  warms the Mathlib cache, runs the test suite. **Done-when this
  notebook runs cold-start in < 10 minutes on T4.**

**Done-when.** `python -m proofrepair compile some_file.lean` returns
correct results, and the test suite passes in CI (GitHub Actions on
ubuntu-latest with elan).

**Risks.**

- Mathlib4 is heavy; full elaboration will blow Colab session time.
  Mitigation: pre-cache Mathlib once, restore from Drive on reboot.
- Lean LSP can hang. Mitigation: hard timeout per file, kill the
  subprocess on timeout.

**Effort.** 3–5 sessions.

---

## Phase 1 — Benchmark curation

**Goal.** A frozen manifest of 50 Lean files, each meeting the
project's structural requirements: a target theorem `T` proved via
3–8 intermediate lemmas, total length 30–80 lines, vocabulary inside
the small-model pretraining distribution.

**Work items.**

- Source candidates from Mathlib4 leaves and from MiniF2F-Lean
  (theorem-only subset). Avoid universe polymorphism, exotic tactics
  (`polyrith`, heavy `decide`), and proofs depending on `noncomputable`
  imports — these make small-model recovery hopeless.
- For each candidate, parse with the Lean LSP and tag spans:
  `theorem T statement`, `lemma L_i statement`, `lemma L_i proof
  body`, `theorem T proof body`, `imports`, `open` lines. These spans
  are inputs to typed corruption.
- Manifest schema: JSONL with one record per file:
  ```
  {file: str, theorem_name: str, theorem_span: [start, end],
   lemma_spans: [...], proof_span: [...], n_lines: int,
   tactic_count: int, vocab_oov_count: int}
  ```
- Hand-check: each file must compile from scratch in the harness in
  ≤ 30 s, and a strong human reader should agree the proof is
  "structured" (i.e. the lemmas carry weight, not decoration).
- Stratification: 25 short (30–50 lines, 3–4 lemmas), 25 long
  (50–80 lines, 5–8 lemmas). Pilot subset = 5 short + 10 long.

**Done-when.** `data/benchmark/manifest.jsonl` exists with 50
entries, every file compiles, and `python -m proofrepair benchmark
verify` passes.

**Risks.**

- Mathlib leaves are often one-line proofs (`by simp`); not useful.
  Mitigation: hand-write a chunk of the long files instead of
  scraping.
- Theorem-statement preservation requires unambiguous span detection.
  Mitigation: re-parse after each candidate edit and assert the span
  hash is unchanged.

**Effort.** 4–6 sessions.

---

## Phase 2 — Corruption taxonomy & typed operators

**Goal.** A library of corruption operators that take `(file, ρ,
locality)` and produce a deterministic-given-seed corrupted file with
the theorem statement provably preserved.

**Work items.**

- Operators (each an instance of `Corruption` ABC):
  1. `TokenMask(ρ)` — replace `ρ` fraction of tokens with `[MASK]`
     (or model-specific mask sentinel).
  2. `TokenReplace(ρ)` — replace with random tokens drawn from the
     file's own vocab.
  3. `LineDelete(ρ)` — drop `ρ` fraction of non-statement lines.
  4. `TacticReplace(ρ)` — replace `ρ` fraction of tactic invocations
     with another tactic of compatible arity (`exact` ↔ `apply`,
     `rw` ↔ `simp only`, etc.). This is the *typed* corruption.
  5. `IdentifierRename(ρ)` — α-rename `ρ` fraction of locally-bound
     identifiers to fresh names.
- Locality control: each operator takes `locality ∈ {scattered,
  clustered}`. Clustered concentrates the noise in one contiguous
  span; scattered samples uniformly.
- All operators must (a) leave the theorem statement byte-identical
  and (b) leave the imports byte-identical. Property-tested.
- A meta-operator `ComposeAtDensity(operators, ρ)` for mixed
  corruption.
- Determinism: every operator takes a seed; two runs with the same
  seed produce byte-identical output.

**Done-when.** Property tests confirm theorem-statement preservation
on all 50 benchmark files × 5 operators × 2 localities × 3 seeds, and
corrupted files round-trip through the harness without crashing it.

**Risks.**

- "Tactic of compatible arity" requires a small Lean-aware static
  analyser. Mitigation: ship a hand-written substitution table for
  the ~20 most common Mathlib tactics; refuse to corrupt if a
  tactic isn't in the table.

**Effort.** 4–6 sessions.

---

## Phase 3 — Small-AR pilot (end-to-end)

**Goal.** Validate the full pipeline by producing the first
`success_rate(ρ)` curve for a small autoregressive baseline.

**Work items.**

- Pick the AR baseline: **DeepSeek-Coder-1.3B-instruct** (4-bit
  quantized) is the default. Fallback: CodeT5+-770M.
- Optional thin QLoRA on LeanDojo's Mathlib data — a 1–2 hour run on
  T4 for the LoRA. Skip if zero-shot already produces a non-flat
  curve.
- Sampler: greedy + temperature 0.2, max 2× original-file tokens.
- Pilot grid: 15 files × `ρ ∈ {0.1, 0.3, 0.6}` × `{TokenMask,
  TacticReplace}` × locality `scattered`. 90 (file, config) cells ×
  3 seeds = 270 trials.
- Logger: one row per trial in `experiments/runs/<run_id>.parquet`
  with `{file, model, corruption, locality, ρ, seed,
  compiles, statement_preserved, total_compile_time_s,
  output_token_count}`.
- Plot: `success_rate(ρ)` for each (corruption, locality), one model.

**Done-when.** A plot exists with at least one non-flat curve (i.e.
`success_rate` strictly decreases between two consecutive `ρ`s for
some configuration). Until that holds, the pipeline isn't measuring
anything.

**Risks.**

- Curve flatlines at zero — small AR can't repair *anything*.
  Mitigation: shrink proofs further, or include `ρ=0` (no
  corruption) sanity-check; if even `ρ=0` fails, the model can't
  read Lean usefully and we change baseline.

**Effort.** 3–5 sessions.

---

## Phase 4 — Diffusion track

**Goal.** Same curve for diffusion checkpoints; first head-to-head
diffusion-vs-AR comparison.

**Work items.**

- **Local checkpoints (T4-fittable).** Pick two:
  - MDLM-110M or SEDD-small (~170M) as the small-scale anchor.
  - SEDD-medium (~440M) or MD4-base as the medium-scale anchor.
  - These are general-text; a thin Lean fine-tune (full FT, ≤ 350M
    fits) on LeanDojo data is in scope.
- **Hosted-API anchor.** Mercury Coder via API, or DiffuCoder-7B if
  we get inference access. Run only at `ρ ∈ {0.3, 0.6}` to anchor.
- Sampler: ancestral sampling at `T` denoising steps, with `T ∈
  {1, 8, 50}` to disentangle "joint denoising" from "iteration".
- The corrupted file is the conditioning input; non-corrupted spans
  are clamped (no resampling) — this is masked-diffusion infilling
  with a fixed mask determined by the corruption operator.
- Same logging schema as Phase 3, plus columns for `denoising_steps`
  and `model_family`.

**Done-when.** A plot overlays AR (Phase 3) and ≥ 2 diffusion curves
on the same axes, on the pilot subset, and the diffusion curves are
not pathologically below AR (if they are, debug — likely a mask /
clamping bug).

**Risks.**

- The pretrained checkpoints are general-text; Lean is OOD. Without
  a fine-tune, denoised tokens are gibberish. Mitigation: fine-tune
  on LeanDojo (this is the only fine-tune we *must* do).
- Sampling step count vs. throughput: 50 steps × hundreds of trials
  is hours. Mitigation: budget `T=8` for the sweep, use `T=50` only
  on a 5-file ablation.

**Effort.** 5–8 sessions.

---

## Phase 5 — EBM track

**Goal.** A pure-EBM model in the comparison, with the verifier
composed in as a hard-constraint energy.

**Work items.**

- Train a **residual `E_θ(file)`** on top of the AR base from Phase 3:
  a small classifier head over the LM's pooled hidden states,
  trained with NCE — positives are uncorrupted benchmark files,
  negatives are corrupted files at varying `ρ`.
- Sampler: importance-resample AR proposals weighted by
  `exp(-E_θ - λ E_lean)`. `E_lean = 0/∞` filter; `λ` controls how
  strictly we filter out non-compiling proposals.
- Optional second variant: small **GFlowNet over edits** — actions
  are token edits inside corrupted spans, reward is `exp(-E_θ -
  λ E_lean)`. Smaller scope, only attempt if residual EBM curve is
  promising.
- Same evaluation grid as Phases 3–4.

**Done-when.** The plot from Phase 4 gains an EBM curve, and we have
a clear answer to: *does composing `E_θ` with `E_lean` raise `ρ*`
above the AR proposal alone?*

**Risks.**

- NCE on a 1.3B base may not have the gradient signal to learn
  `E_θ`. Mitigation: try ranking loss (BT model on (clean, corrupt)
  pairs) as a fallback.
- Sampling-cost blowup: importance reweighting needs many proposals.
  Mitigation: cap proposals at 16 per trial.

**Effort.** 5–7 sessions.

---

## Phase 6 — Full ρ-sweep

**Goal.** Produce the final paper-grade plots over the full benchmark.

**Configuration grid.**

| Axis | Values |
|------|--------|
| Files | all 50 |
| `ρ` | {0.05, 0.10, 0.20, 0.40, 0.60, 0.80} |
| Corruption | all 5 operators |
| Locality | scattered, clustered |
| Models | small AR, ≥ 2 diffusion (Phase 4), EBM (Phase 5), + hosted-API anchor at 2 `ρ`s |
| Seeds | 3 per cell |

**Work items.**

- Pre-flight: estimate wall-clock from Phase 3–5 throughput; if
  > 24 h, drop one corruption type or reduce `ρ` granularity to
  {0.1, 0.3, 0.5, 0.7} (4 values). Record the decision in the
  experiment log.
- Run as a single sweep, snapshot results to disk every N trials so
  a Colab disconnect loses < 30 minutes of work.
- Compute `ρ*` per (model × corruption × locality × proof-length)
  cell — defined as the largest `ρ` whose mean success rate is
  ≥ 0.5 (95% CI lower bound).
- Plot family: small-multiples grid of `success_rate(ρ)` curves, one
  panel per (corruption × locality), models overlaid.

**Done-when.** All cells run, every (model × corruption × locality)
has a `ρ*` (possibly `0` or `ρ_max` as boundary cases), and a
single LaTeX-friendly figure summarises the full result.

**Risks.**

- T4 session timeouts kill long runs. Mitigation: chunk the sweep
  into `ρ`-bands and persist between bands; consider Colab Pro for
  this phase specifically.
- Hosted-API rate limits and cost. Mitigation: cap API anchor
  trials at 50 total (10 files × 1 corruption × 1 locality × 2 `ρ` ×
  ~2 seeds + a few duplicates).

**Effort.** Wall-clock 1–3 days; calendar 5–10 sessions including
debugging.

---

## Phase 7 — Verifier-in-the-loop ablation

**Goal.** Quantify how much access to `E_lean` *during sampling* (vs.
only at the end) raises `ρ*`. This is the key signal for the MCP
tool's design.

**Work items.**

- Three sampler modes per model:
  1. **No verifier**: sample once, accept whatever comes out.
  2. **Verifier-as-filter**: sample `k` candidates, return the first
     that compiles, fail if none.
  3. **Verifier-as-guidance**: every `s` denoising / EBM steps,
     compile the partial output and use the error trace to
     re-mask the offending span and resample. Stop when compiles or
     after `n` rounds.
- Run on a 15-file × 4-`ρ` × 2-corruption subset for cost.
- Report `ρ*_no_verif`, `ρ*_filter`, `ρ*_guidance` per model.
- Headline number: `Δρ* = ρ*_guidance − ρ*_no_verif`.

**Done-when.** A table reports `Δρ*` per model, and we have a clear
decision rule: *for the MCP tool, do we wire the verifier into the
sampler?* (Almost certainly yes, but the magnitude matters.)

**Risks.**

- Verifier-as-guidance is the most novel contribution and the most
  bug-prone. Mitigation: start with mode (2), only attempt mode (3)
  if (2) is stable.

**Effort.** 4–6 sessions.

---

## Phase 8 — MCP tool prototype

**Goal.** A locally-runnable MCP server that exposes diffusion proof
repair as a tool to Claude Code.

**Work items.**

- Server skeleton (Python MCP SDK), exposing two tools:
  - `repair_proof(file_path, region_lines, max_steps)` — runs the
    best-performing sampler from Phases 4–7 on the region,
    returns the repaired text and a list of remaining errors.
  - `autoformalize_chunk(informal_text, lean_context, target_size)`
    — runs diffusion from a heavily-masked initial state of length
    `target_size`, using `lean_context` as conditioning. Returns
    candidate chunks ranked by `E_θ + E_lean`.
- The chunk size for `autoformalize_chunk` is set from `ρ*`: chunks
  whose expected initial error density ≤ `ρ*`.
- Integration test: drop the MCP server into a fresh Claude Code
  session against a held-out Mathlib repo. Pose 5 multi-error repair
  tasks. Compare:
  - Claude Code alone vs.
  - Claude Code + `repair_proof` MCP tool.
  Record number of edit cycles to green and total wall-clock.
- Document: a `README` with install + usage, plus a sample
  `claude.md` snippet showing how to invoke.

**Done-when.** The held-out integration test shows fewer edit cycles
to green with the MCP tool than without, on at least 3 of the 5
tasks.

**Risks.**

- The model's repair quality isn't good enough to be useful as a
  tool; the integration test fails. Mitigation: this is a real
  outcome — if `ρ*` is low (e.g. ≤ 0.1), the right paper conclusion
  is "diffusion proof repair is below the bar for tool deployment
  at our compute scale" and we ship the measurement, not the tool.

**Effort.** 4–6 sessions if Phase 7 results are favourable; the
phase is conditional on a positive `Δρ*`.

---

## Cross-cutting concerns

**Reproducibility.** Every experiment run is parameterised by a YAML
config and a seed; configs are git-committed; results are written to
`experiments/runs/<git_sha>__<config_sha>__<seed>/`.

**Logging.** Use Weights & Biases (free tier is enough) for the
sweeps. Each trial is a row; aggregations happen offline.

**Repo layout.**

```
proofrepair/
├── pyproject.toml
├── lakefile.lean
├── lean-toolchain
├── src/proofrepair/
│   ├── lean/        # compile, parse, span tagging
│   ├── corrupt/     # operators
│   ├── models/      # AR, diffusion, EBM wrappers
│   ├── samplers/    # plain / verifier-filter / verifier-guidance
│   ├── eval/        # success_rate, rho_star, plotting
│   ├── mcp/         # tool server (Phase 8)
│   └── cli.py
├── data/
│   ├── benchmark/manifest.jsonl
│   └── lean_files/
├── experiments/
│   ├── configs/*.yaml
│   └── runs/
├── notebooks/colab/
└── tests/
```

**What to *not* build.** Skip a custom dataloader framework, skip a
config-management library beyond plain YAML+pydantic, skip a web UI
for results — matplotlib + a final notebook is enough. The point is
the curves, not the tooling.

**Decision points where we re-plan.**

1. End of Phase 3: if AR pilot curve is flat, restructure benchmark
   (smaller proofs) before continuing.
2. End of Phase 4: if diffusion underperforms AR even after
   fine-tune, re-pick checkpoints (e.g. swap MDLM for Dream-Coder
   distilled to a smaller size) rather than push deeper.
3. End of Phase 7: `Δρ*` decides whether Phase 8 ships or the
   project ends as a measurement-only paper.

---

## Timeline (calendar weeks, indicative)

| Weeks | Phases |
|-------|--------|
| 1–2 | Phase 0 + 1 |
| 3 | Phase 2 |
| 4 | Phase 3 |
| 5–6 | Phase 4 |
| 7 | Phase 5 |
| 8 | Phase 6 (full sweep — uses Colab Pro) |
| 9 | Phase 7 |
| 10–11 | Phase 8 + writeup |

Total: ~11 weeks of focused part-time work. The Colab Pro upgrade
matters from week 6 onward.
