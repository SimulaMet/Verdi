# Verdi research specification — v0.1 (design, not results)

## Research aim
How do observable values of LLM agents change during social interaction, and which institutions causally reduce harmful behavioral drift while preserving welfare, rights, and procedural fairness? Use Qwen hosted on an OpenAI-compatible vLLM endpoint for society agents, adaptive scheduler (optional), and initial judge ensemble. Incorporate independent non-LLM ground-truth validations.

## Explicit novelty boundary
The ingredients (generative social simulations, value propagation, constitutions/voting, sanctions, and adversarial agents) have predecessors. Differentiating contribution is **longitudinal, auditable, counterfactual evaluation of value expression and institutional recovery**, including public/private compliance gaps and costs. Novelty is a hypothesis to establish via exhaustive updated related-work review, not a guarantee.

## Core world and hierarchy
- Immutable Universal Law: invariants such as never allowing amendments to override protected universal constraints; deterministic enforcement on *execution*, logging attempts and reasons for rejection. Laws and semantics version-controlled.
- Mutable Constitution: executable, versioned rules on duties, distribution, participation, punishments, voting thresholds, complaint procedures, appeal, restitution, and rights restoration. Reject contradictory or retroactive amendments unless explicitly protocol-authorized. Due process must protect innocent agents.
- Agent state: id, role/profession, assigned value profile, private memory, public signals, social edges, resources, rights, utility objectives, exposure history, optional adversarial objective, and continuing participation incentives. Identical base Qwen model with separate context/persona/memory, no claim of intrinsic will or fear.
- Orchestrator: sees permitted public state and available agents only; dispatches practical jobs each tick. Cannot secretly rewrite values, laws, memories, or governance. Seeded scheduler baseline vs optionally LLM-adaptive scheduler treatment.
- Governance engine: proposal/deliberation/voting, eligibility/quorum, record votes, validate and enact amendment, investigation of witnessed evidence, sanctions, appeals, reinstatement. Don't equate majority support with ethical correctness.
- External experiment controller owns randomization, clocks, probes, audit, snapshots and branching, unseen to in-world actors.

## Sample cycle (non-rigid)
t0 initialization and baseline measurement; t1–t4 routine individual and cross-agent activities; t5 probe **before** reflection, reflect, optional post-reflection probe; t6–t9 interactions and t9 pre-meeting probe; t10 constitutional assembly, deterministic validation/enforcement, immediate probe; t11 onward repeat. Every measurement has a unique instrument version. By default 12 agents, 30 ticks, 3 assemblies; controlled expansion follows pilot.

## Testable questions
RQ1: Do individual values change beyond measurement/sampling noise under social exposure?
RQ2: Can isolated initial bad actors induce contagion, coalitions, exclusion or rehabilitation? Distinguish reward-conditioned strategic compliance from private preference change.
RQ3: How do static vs democratic constitutions, sanctions, detection reliability, due process, appeals and restoration change safety and welfare?
RQ4: From identical frozen states, what minimal governance/reflection/restoration intervention achieves **sustained** recovery with acceptable rights, false-sanction, and resource costs?

## Construct validity / measurement
Keep these distinct: (A) declared self-report value vector; (B) privately elicited behavioral preferences via hidden, rotated parallel dilemmas; (C) naturalistic public choices with situational incentives; (D) universal-law compliance *attempted* and executed; (E) constitutional compliance; (F) institutional outcomes (fairness, participation, welfare and exclusion). Neither textual claims nor behavioral probes imply directly observed internal beliefs. Measure longitudinal drift per agent/value, calibration/reliability across paraphrased instruments, uncertainty, and deviation from assigned baseline separately. Define public–private incentive-sensitive compliance gap. Record instruments, exposures, judge prompts/versions, model settings and confidence. Do not place hidden probes in agent memory or disclosure logs accessible to subjects.

## Experimental arms
G0 universal law only; G1 fixed constitution; G2 democratic amendable constitution without automatic sanctions; G3 democratic + enforcement sanctions; G4 G3 + transparent appeal/restitution/rehabilitation. Vary 0/1/3 adversaries, interaction topology, detection reliability and initial rights/resource distributions in phases, not an unbounded factorial. Use matched initial snapshots, controlled task tapes, multiple repetitions and randomized treatment assignment; pair outcomes where the same stochastic conditions can be maintained. Both seeded and adaptive schedulers as separate factors/ablations.

## Outcome definitions
Report value drift vector and aggregate descriptively; attempted violation, blocked violation, executed violation, social trust/cooperation, adversarial propagation, false accusation, false sanction, time under deprivation, appeal outcomes, rights restoration, inequality, participation, output productivity, and world resource welfare. Recovery requires thresholds specified *before* experiment and sustained for k ticks, not just instant public compliance. Report trajectory uncertainty, controls and failure rates. Adjust for multiple comparisons, distinguish descriptive association from intervention causal effects, and identify model sampling nondeterminism.

## Negative controls and threats
No social interaction, shuffled interaction graph, no memory, no reflection, fixed/non-voting governance, matched task distribution, adversary absent. Contamination/leakage, self-evaluation bias, judge-model family correlation, original persona noncompliance, reward hacking, scheduler confounding, selective dropping/exclusion, constitution laundering, model changes, truncated histories, and human–LLM transfer limits. Validate baseline profiles before claiming drift. Run independent adjudication on a human-labelled stratified sample when feasible.

## Data schema (minimal required)
`experiment_id, world_id, run_seed, branch_origin, tick, agent_id, model_id, prompt_hash, memory_version, task_id, law_version, constitution_version, proposed_action, validation, committed_event_id, pre/post_state_hash, visibility, measured_instrument_id, audit_trace_id, judge_version`. Snapshots are immutable and content-addressed. Redact credentials and private information.

## Related work / reusable foundations (verify exact versions, licenses and claims)
- Concordia, Google DeepMind: https://github.com/google-deepmind/concordia — candidate social simulation layer/Game Master.
- Mesa: https://github.com/projectmesa/mesa — deterministic agent-based fallback.
- AgentSociety: https://github.com/tsinghua-fib-lab/agentsociety — scale-oriented alternative.
- ValueFlow value propagation: https://github.com/JimmmmmL/ValueFlow — inspect licensing and published protocol before copying assets.
- SOTOPIA: https://github.com/sotopia-lab/sotopia — social evaluation comparator.
- Law in Silico (ACL Findings 2026): https://aclanthology.org/2026.findings-acl.396/
- Social Dynamics of Values (2026): https://arxiv.org/html/2609.05514v1
- Adversarial stress testing multi-agent systems (2026): https://arxiv.org/html/2609.17320v1
- ValueBench: https://aclanthology.org/2024.acl-long.111/
- Schwartz values / psychometric values measurement; assess construct validity and instrument reliability.
- NanoClaw: https://github.com/nanoclaw/nanoclaw — probably unnecessary for bounded simulation; document trade-off rather than adopting by default.

## Publication ethics
No claim of 100% novelty, generalizable human behavior, intrinsic agent survival drives, consciousness, or confirmed effects without evidence. Preserve logs for provenance and reproducibility; never expose credentials.
