# DEN-3179 chacha20 lock remediation evidence

This record documents the narrow lock-only remediation that removed the yanked
`chacha20` 0.10.1 dependency from draft pull request #177 without weakening the
repository audit policy.

## Reviewed change

```text
Cargo.lock format: 3 -> 3
chacha20 version: 0.10.1 -> 0.10.2
chacha20 checksum:
  d524456ba66e72eb8b115ff89e01e497f8e6d11d78b70b1aa13c0fbd97540a81
  -> 65c35e4b699c7e15ccbe7ee35c005e4fc0a278d22238a2857e6ce2dadeda1b06
```

The source remained `registry+https://github.com/rust-lang/crates.io-index` and
the dependency list remained exactly:

```text
cfg-if
cpufeatures 0.3.0
rand_core 0.10.1
```

Every other Cargo package record was byte-semantically unchanged. The textual
lock diff contains only the package version and checksum replacements above.
The final reviewed `Cargo.lock` SHA-256 is:

```text
0d84eb5ffb2957e3e195185121a97241161e995b7d881dfd6d8e458d8e4413c8
```

## Exact-head evidence

The one-time, exact-head, feature-branch-only remediation workflow ran against
commit `7d7368206ae4947daa26098beb82923e5eb3dfc1` and completed successfully as
workflow run `33695040776`, job `100461993325`.

It proved:

- the original and resulting lock format are both version 3;
- only the one `chacha20` package record changed;
- the textual diff is exactly two replaced values;
- `cargo test --locked --all` passes; and
- the unchanged command
  `cargo audit --deny warnings --ignore RUSTSEC-2023-0071` passes.

The retained workflow artifact is `9871483990`, digest
`sha256:64ac935c84fca8f8b0de9f13cb2067351fd48571c3d942ed04a660aa4a8be943`,
with the generated evidence JSON, exact diff, and resulting lock. The one-time
workflow deleted itself in the same feature-branch commit that landed the
reviewed lock.

## Boundaries

This remediation does not add an audit exception, suppress a warning, change a
runtime dependency feature, merge the pull request, deploy the worker, activate
the Cloudflare scheduler, or use a credential pasted into chat. Pull request
#177 remains draft until all exact-head checks and independent review gates are
satisfied.
