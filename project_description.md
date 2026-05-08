# Diffusion Models for Lean Proof Repair — Density Threshold Study

## Motivation

Agentic systems like Claude Code degrade as the **density of errors** in a
file rises: each fix may invalidate the assumptions used to plan the next
one, and the agent ends up oscillating. The standard mitigation is to
*plan first, then implement progressively*, so that errors stay localized
and each fix is made against a mostly-correct context.

Diffusion models offer a different trade-off. A discrete (or text)
diffusion model denoises many tokens jointly in each step, so in
principle it can resolve a high-density cluster of errors in one shot,
where an autoregressive agent would have to make N sequential local
fixes. **The empirical question is: how dense can the corruption get
before the diffusion model fails?**

This repository is the experimental scaffolding for that study,
specialized to **Lean 4 proofs** (rich typing makes corruption +
verification cheap and unambiguous).

## Core question

> What is the maximum corruption density `ρ*` (fraction of tokens / lines
> / tactics replaced) at which a given diffusion model can still
> recover a *valid* Lean file from the corrupted version?

`ρ*` should be measured as a function of:

- **Granularity of corruption**: token-level random substitution,
  line-level deletion, tactic-level substitution, identifier renaming.
- **Locality of corruption**: clustered (a contiguous block) vs.
  scattered (uniform random) — same `ρ` can be much harder when
  scattered because no clean anchor remains.
- **Proof complexity**: short tactic blocks vs. long structured proofs
  that depend on many intermediate lemmas.
- **Model size / family**: comparing different diffusion checkpoints,
  and comparing against an autoregressive baseline at matched
  parameter count.

## The evaluation problem and a proposed workaround

The naive setup — *take a compiling file, corrupt it, ask the model to
denoise, check token-level overlap with the original* — is broken: a
valid alternative proof should count as success but will look different
on the surface.

**Proposed protocol.** Construct a Lean file with the following shape:

1. A **target theorem** `T` whose statement is the contract.
2. A **long proof of `T`**, factored through many intermediate lemmas
   `L_1, …, L_k` proved earlier in the file.
3. Corrupt **everything except the statement of `T`** (and its imports)
   at density `ρ`. The lemma *statements* and *proofs* are all in scope
   for corruption; only `T`'s statement is preserved.

The model is asked to denoise the corrupted file. **Success criterion:
the output type-checks under Lean and the statement of `T` is
syntactically unchanged.** This guarantees the model has produced *some*
valid proof of the same theorem, without having to match the original
proof line-by-line. Any path through Lean's logic that closes `T` is
acceptable.

This sidesteps semantic-equivalence judgments at the cost of allowing
the model to "cheat" by collapsing the proof — e.g. skipping all
intermediate lemmas and proving `T` directly. We mitigate with two
controls:

- **Time/length budget**: cap total tokens so the model can't expand a
  hard proof into a single monolithic tactic.
- **Lemma-locked variant**: also fix the *statements* (not proofs) of
  some `L_i`, forcing the model to re-derive the structured proof.

## Experimental dimensions

| Axis | Values |
|------|--------|
| Diffusion model | candidate checkpoints — see `bibliography.md` |
| EBM variant | residual EBM / discrete-score EBM / GFlowNet |
| AR baseline | matched-size autoregressive code model |
| Verifier-in-the-loop | off / on (Lean called during sampling, not just at end) |
| Corruption type | token-mask, token-replace, line-delete, tactic-replace, identifier-rename |
| Corruption locality | clustered / scattered |
| Density `ρ` | sweep `{0.05, 0.1, 0.2, 0.4, 0.6, 0.8}` |
| Proof length | short (< 20 lines), medium (20–100), long (> 100, multi-lemma) |
| Lemma locking | statements free / statements locked |

The output of one experimental cell is a **success rate** over a
benchmark of corrupted files at that `ρ` and configuration.

## Deliverable

A curve `success_rate(ρ)` per (model × corruption type × locality ×
proof length) cell, and the inferred threshold `ρ*` (e.g. the largest
`ρ` with success rate ≥ 0.5).

### Downstream deliverable: an MCP tool for chunked repair / autoformalization

If the density study shows that a diffusion model handles useful values
of `ρ` (e.g. ≥ 0.3 on tactic-level corruption with multi-lemma proofs),
then a natural product is an **MCP tool callable by Claude Code** that
exposes the diffusion repair as a primitive:

- `repair_proof(file, region)` — given a Lean region with many
  simultaneous errors, run diffusion denoising under Lean as oracle and
  return a repaired region. The agent calls this *instead* of looping
  through one-error-at-a-time edits.
- `autoformalize_chunk(informal_text, context)` — given a paragraph of
  informal math and the current Lean context, produce a chunk of Lean
  by running diffusion from a heavily-masked starting state. This lets
  Claude Code formalize at paragraph granularity, where the joint
  denoising can resolve cross-token dependencies (variable names,
  tactic ordering, lemma references) in one step instead of
  left-to-right.

The empirical `ρ*` directly governs the chunk size we expose: the tool
should request chunks whose expected error density after the agent's
first attempt sits below `ρ*`. So the measurement is not just academic
— it sets the contract for how this MCP tool integrates with an
autoregressive agent.

## Alternative model family: energy-based models

Diffusion is one way to denoise; **energy-based models (EBMs)** are
another, and arguably a more natural fit for this task because Lean
itself supplies a hard energy function for free:

```
E_lean(file) = 0  if file type-checks
             = ∞  otherwise
```

Any sampler that minimizes energy can plug `E_lean` in as a hard
constraint. Score-based diffusion is mathematically a stack of EBMs at
different noise levels; pure-EBM approaches differ in that they (a)
allow composition of multiple energies — a learned `E_θ(file)` plus
the verifier's `E_lean` — and (b) sample via MCMC / Langevin /
GFlowNets rather than a fixed denoising schedule.

Concretely, three EBM tracks worth comparing against the diffusion
sweep:

1. **Residual EBM on top of a base proof LM.** Train an `E_θ` to
   discriminate compiling vs. corrupted Lean files; sample by
   importance-reweighting an autoregressive base model's proposals
   (Deng et al. 2020). The residual energy soaks up the "is this
   actually a proof" signal that next-token loss is bad at.

2. **Discrete score matching / concrete score.** Recent work makes EBM
   training over discrete sequences tractable (Meng et al. 2022, SEDD
   in this lineage). A pure discrete EBM `E_θ(file)` can be sampled
   via MCMC with the verifier as a hard-rejection filter at each step.

3. **GFlowNet over proof trees / file edits.** GFlowNets sample
   structured objects in proportion to a reward; the reward here is
   `exp(-E_lean) ⋅ exp(-E_θ)`. This is closest to current AR proof
   search but with explicit credit assignment over the whole file
   instead of left-to-right.

The same evaluation protocol applies: corrupt at density `ρ`, sample,
check that `T`'s statement is preserved and the file type-checks.
Adding EBMs lets us also report:

- **`ρ*` for diffusion vs. EBM vs. AR** at matched compute.
- **The verifier-loop cost**: how much does access to `E_lean` during
  sampling (not just at the end) raise `ρ*`? This isolates "structural
  prior from training" vs. "constraint-aware sampling" — and
  pre-answers the question of whether the MCP tool should run the
  type-checker inside its inner loop.

## Open design questions

1. **Source of compiling Lean files.** Mathlib snippets? Hand-curated?
   A subset of `mathlib4`'s leaf lemmas keeps proofs short and
   self-contained.
2. **Diffusion model choice.** Are there off-the-shelf code-trained
   discrete-diffusion checkpoints? Or do we need to fine-tune one (e.g.
   on Lean) before measuring?
3. **Compute envelope.** How many denoising steps to allow per file?
   Match this against the AR baseline's token budget honestly.
4. **Corruption that preserves syntactic shape.** Replacing a tactic
   with random tokens almost certainly breaks parsing; replacing it
   with another tactic of the same arity is more informative. Worth
   defining a *typed* corruption operator.
5. **Multi-step denoising vs. single shot.** A diffusion model run for
   T steps already does iterative refinement; comparing T=1 vs. T=50
   isolates the "joint denoising" benefit from sheer iteration.

## Compute envelope (single T4 on Google Colab)

This project runs on **one NVIDIA T4 (16 GB VRAM, ~65 fp16 TFLOPS)**
via Colab, with session limits of ~12 hours. That hard constraint
reshapes the experimental plan as follows.

**What does *not* fit on T4:**
- Goedel-Prover-V2 (8B), DeepSeek-Prover-V2, Llemma-7B, DiffuCoder-7B,
  Dream-Coder-7B at fp16 — weights alone are ≥ 14 GB.
- Any full fine-tune above ~350M parameters.
- LoRA fine-tuning of a 7B model with 4-bit quantization (QLoRA) is
  technically possible but at our token lengths and Lean-in-the-loop
  cost, it will eat the whole 12-hour session per training run.

**What fits comfortably:**
- **Inference at ≤ 1B (fp16)** or **≤ 3B (4-bit)**, including
  generation under masked-diffusion samplers.
- **LoRA fine-tuning at ≤ 1.3B (4-bit base + small LoRA rank)**.
- **Full fine-tuning at ≤ 350M**.
- Lean elaboration itself is CPU-bound, not GPU-bound, so the verifier
  is free GPU-wise — but it will dominate wall-clock (seconds per
  file).

**Revised model slate.** We pick small open checkpoints we can run
locally and treat large frontier models as hosted-API baselines we
query sparingly:

| Role | Local on T4 | Hosted-API baseline |
|------|-------------|---------------------|
| Small diffusion LM | SEDD-small/medium (~170M / 440M), MDLM (~110M), MD4-small | Mercury Coder (API) |
| Small AR LM | DeepSeek-Coder-1.3B / CodeT5+-770M, optionally with QLoRA on Lean | Goedel-Prover-V2, DeepSeek-Prover-V2 (API or hosted endpoint) |
| EBM track | Residual `E_θ` on top of the small AR base; small GFlowNet | — |

Frontier models are queried only at fixed `ρ` values to anchor the
curve, not at every cell of the sweep. This keeps the ρ-sweep a
locally-replicable artifact while still positioning against SOTA.

**Throughput budget.** With Lean compilation at ~3–10 s per file and
~5000 cells in the full sweep (50 files × 6 `ρ` × 5 corruption types ×
3 seeds), the verification alone is ~5–15 hours. Build the harness so
that:

- Lean compilation runs in a separate process pool, not in the model
  call.
- Failed compilations are cached by hash so reruns are cheap.
- The benchmark pilot uses a **15-file × 3-`ρ` × 2-corruption-type
  subset** before the full sweep. Roughly a 1-hour pilot, ~10-hour
  full sweep.

**Implication for proof complexity.** The "long proof with many
intermediate lemmas" target needs to be sized so that small models can
*plausibly* succeed at low `ρ`, otherwise the curve flatlines at zero
and we measure nothing. Concretely: target proofs of 30–80 lines, 3–8
lemmas, vocabulary inside the model's pretraining distribution
(Mathlib basics, not exotic tactics). A model that fails at `ρ=0`
yields no signal — the goal is to find the regime where these models
start to succeed and then stress them.

### Upgrade path: Colab Pro

Colab Pro typically swaps the T4 for an **A100 (40 GB)** or **V100
(16 GB)** with longer sessions (~24 h) and background execution. The
A100 in particular changes what is locally feasible:

- 7B-class models (DiffuCoder, Dream-Coder, Goedel-Prover-V2,
  DeepSeek-Coder-7B) fit in fp16 at full precision for *inference*.
- 7B QLoRA fine-tuning becomes practical within a single session.
- Background execution removes the "session timeout in the middle of
  a sweep" failure mode.

Decision rule for upgrading: stay on T4 for the **pilot** (small
models, 15-file × 3-`ρ` × 2-corruption-type subset) and the **harness
shakedown**. Upgrade to Pro once the harness is stable and we are
ready to (a) fine-tune a 7B diffusion code model on Lean, or (b) run
the full ρ-sweep against a 7B AR baseline locally instead of via API.
Spending Pro hours on debugging the Lean-verifier loop or on the
corruption taxonomy would be waste; spend them on the runs that
actually need the headroom.

## Status

- [x] Project framing written down (this file).
- [ ] Bibliography pass (in progress — see `bibliography.md`).
- [ ] Pick 2–3 candidate diffusion checkpoints to benchmark.
- [ ] Build the corruption + Lean-verify harness.
- [ ] Curate a small (~50 file) benchmark of compiling Lean theorems
      with structured multi-lemma proofs.
- [ ] Run the sweep, plot `success_rate(ρ)`.
