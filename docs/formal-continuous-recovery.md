# Formal verification: continuous artifact-recovery supervisor

Tracking: DEN-3973, GitHub issue #273. The completed implementation issue
DEN-3179 remains closed.

## Production surface

The proof refines these executable definitions in
`tools/run_continuous_artifact_recovery_pool.py`:

- `worker_count`;
- `worker_id_prefix`;
- `RestartBudget.record`;
- `RestartBudget.reset`; and
- the supervisor ordering represented by running children, pending respawns,
  stop requests, terminate grace, kill grace, and terminal failure.

The change also closes two input-boundary discrepancies found during the
last-25-day recovery audit:

1. Python's permissive `int()` grammar accepted noncanonical configuration
   such as `+1`, surrounding whitespace, and underscores.
2. Trimming the worker-ID prefix accepted identity text that could contain
   whitespace, controls, path separators, or non-ASCII characters.

All integer settings now require canonical unsigned decimal text. Worker
prefixes are 1–96 ASCII characters, begin with an alphanumeric character, and
otherwise contain only letters, digits, `.`, `_`, or `-`.

## Executable bounded model

`formal/continuous_recovery_supervisor.py` explores every reachable state for
one, two, and three worker slots through event sequences of length ten. The
verification uses a deliberately small restart bound of two and a backoff cap
of four seconds so the complete bounded graph can be enumerated.

The model actions are:

- worker crash;
- stable worker exit;
- restart-window expiry;
- backoff completion and respawn;
- operator stop;
- child exit during shutdown;
- terminate-grace expiry; and
- kill-grace expiry.

The exact depth-ten graph is part of the admission contract:

| Workers | Reachable states | Transitions |
|---:|---:|---:|
| 1 | 80 | 101 |
| 2 | 834 | 1,416 |
| 3 | 4,644 | 9,382 |

A count change is not automatically wrong, but it requires an explicit model
and evidence review rather than silently widening the state machine.

## Safety invariants

The checker rejects any reachable state violating these properties:

1. There are one through three slots and no more than three live children.
2. A slot represents at most one child generation.
3. Once stopping begins, no pending backoff or later respawn is possible.
4. The third crash inside the bounded two-restart window exhausts the budget,
   records failure, clears pending respawns, and begins shutdown.
5. Backoff is exactly `min(cap, 2^(attempts - 1))`.
6. A stable runtime or expired restart window resets prior crash history.
7. Terminate grace must elapse before kill grace.
8. A process surviving kill grace becomes an explicit terminal failure rather
   than an unbounded wait.

The checker also requires concrete witnesses for a clean operator shutdown,
restart-budget exhaustion, and kill-timeout termination at every worker count.

## Production refinement

The same run imports the production module rather than copying its arithmetic.
It checks:

- valid worker counts and strict rejection of malformed/noncanonical inputs;
- valid and invalid worker-ID prefixes;
- every restart attempt for several restart budgets and backoff caps;
- sliding-window expiry; and
- stable-runtime reset.

The receipt binds both the model and production source with SHA-256 digests.

## TLA+ peer specification

`formal/ContinuousRecoverySupervisor.tla` expresses the same actions and
state invariants independently. `formal/ContinuousRecoverySupervisor.cfg`
records the reviewed finite constants.

The dependency-free Python explorer is the executable CI proof. The repository
does **not** claim that the TLA+ theorem is model-checked by TLC in this lane.
Adding a pinned TLC or Apalache execution is a future strengthening and must
not replace the production refinement.

## Evidence and commands

```bash
python3 -m py_compile \
  tools/run_continuous_artifact_recovery_pool.py \
  formal/continuous_recovery_supervisor.py \
  tests/test_continuous_recovery_formal.py

python3 formal/continuous_recovery_supervisor.py \
  --depth 10 \
  --receipt /tmp/continuous-recovery-formal-receipt.json

python3 -m unittest -v tests/test_continuous_recovery_formal.py
```

CI retains the receipt for 30 days.

## Claim boundary

This proves bounded safety and refinement of the listed production functions.
It does not prove:

- unbounded liveness or fairness;
- completeness or authenticity of ChatGPT, Codex, GitHub, Linear, or file
  sources;
- production deployment or credential availability;
- correctness of distributed filesystems or advisory locks;
- absence of operating-system, kernel, or container-runtime faults; or
- successful recovery of every historical task.

Production activation remains separately gated.
