# Contributing

1. Read `agents.md` and the relevant environment/control-plane files.
2. Make the smallest semantically complete change on a feature branch.
3. Never add a plaintext secret or make a placeholder artifact promotable.
4. Preserve the explicit dependency graph and keep environment documents structurally aligned.
5. Run `make agent-check` and review the rendered plans and blocker changes.
6. Describe compatibility, rollout, rollback, security, cost, and operational impact in the pull request.
7. Attach real cluster evidence only when it was produced from the exact reviewed commit; do not treat a render as convergence.
