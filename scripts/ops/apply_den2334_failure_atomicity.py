#!/usr/bin/env python3
"""Apply the reviewed DEN-2334 post-merge failure-atomicity correction.

This is a temporary branch-only transformation helper. It validates exact anchors,
modifies only the four reviewed product files, and is deleted before PR review.
"""

from __future__ import annotations

from pathlib import Path

EXPECTED_FILES = {
    Path(".github/workflows/daily-portfolio-delivery-state.yml"),
    Path("docs/daily-portfolio-delivery-state.md"),
    Path("src/daily_portfolio_delivery.rs"),
    Path("tests/daily_portfolio_delivery_state.rs"),
}


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def unique_index(lines: list[str], needle: str, *, start: int = 0) -> int:
    matches = [index for index in range(start, len(lines)) if lines[index] == needle]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one anchor {needle!r} from line {start + 1}; "
            f"found {len(matches)}"
        )
    return matches[0]


def replace_inclusive(
    lines: list[str],
    start_anchor: str,
    end_anchor: str,
    replacement: str,
) -> list[str]:
    start = unique_index(lines, start_anchor)
    end = unique_index(lines, end_anchor, start=start)
    if end < start:
        raise RuntimeError("invalid replacement bounds")
    return [
        *lines[:start],
        *replacement.splitlines(keepends=True),
        *lines[end + 1 :],
    ]


def replace_exclusive_end(
    lines: list[str],
    start_anchor: str,
    end_anchor: str,
    replacement: str,
) -> list[str]:
    start = unique_index(lines, start_anchor)
    end = unique_index(lines, end_anchor, start=start + 1)
    return [
        *lines[:start],
        *replacement.splitlines(keepends=True),
        *lines[end:],
    ]


def update_source() -> None:
    path = Path("src/daily_portfolio_delivery.rs")
    lines = read_lines(path)
    lines = replace_inclusive(
        lines,
        "        self.next_fence = self\n",
        "        self.leases.insert(run_key.to_owned(), token.clone());\n",
        """        // Compute every fallible value before mutating durable state. An invalid
        // caller timestamp must not consume a fence or partially claim a lease.
        let next_fence = self
            .next_fence
            .checked_add(1)
            .ok_or(DeliveryStateError::CounterOverflow)?;
        let expires_at_ms = now_ms
            .checked_add(ttl_ms)
            .ok_or(DeliveryStateError::CounterOverflow)?;
        let token = LeaseToken {
            run_key: run_key.to_owned(),
            owner: owner.to_owned(),
            fence: next_fence,
            expires_at_ms,
        };
        self.next_fence = next_fence;
        self.leases.insert(run_key.to_owned(), token.clone());
""",
    )
    lines = replace_inclusive(
        lines,
        "                record.attempts = record\n",
        "                record.status = DeliveryStatus::Delivering;\n",
        """                // Preserve compare-and-set failure atomicity: calculate both
                // counters before changing either field.
                let attempts = record
                    .attempts
                    .checked_add(1)
                    .ok_or(DeliveryStateError::CounterOverflow)?;
                let generation = record
                    .generation
                    .checked_add(1)
                    .ok_or(DeliveryStateError::CounterOverflow)?;
                record.attempts = attempts;
                record.generation = generation;
                record.status = DeliveryStatus::Delivering;
""",
    )
    lines = replace_exclusive_end(
        lines,
        "fn validate_identifier(value: &str) -> Result<(), DeliveryStateError> {\n",
        "fn validate_error_summary(value: &str) -> Result<(), DeliveryStateError> {\n",
        """fn contains_credential_prefix(value: &str, prefix: &str) -> bool {
    value
        .split([':', '/'])
        .any(|segment| segment.starts_with(prefix))
}

fn validate_identifier(value: &str) -> Result<(), DeliveryStateError> {
    // Enforce the bound before allocating the lowercase inspection copy.
    if value.is_empty() || value.len() > MAX_IDENTIFIER_BYTES {
        return Err(DeliveryStateError::InvalidIdentifier);
    }

    let lower = value.to_ascii_lowercase();
    let credential_shaped = [
        "ghp_",
        "gho_",
        "ghu_",
        "ghs_",
        "ghr_",
        "github_pat_",
        "sk-",
        "xoxb-",
        "xoxa-",
        "xoxp-",
        "xoxr-",
        "xoxs-",
    ]
    .iter()
    .any(|prefix| contains_credential_prefix(&lower, prefix))
        || lower.contains("api_key=")
        || lower.contains("apikey=")
        || lower.contains("access_token=")
        || lower.contains("auth_token=")
        || lower.contains("bearer=")
        || lower.contains("token=")
        || lower.contains("password=")
        || lower.contains("secret=");
    let valid = !credential_shaped
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric()
                || matches!(byte, b'-' | b'_' | b'.' | b':' | b'/')
        });
    if valid {
        Ok(())
    } else {
        Err(DeliveryStateError::InvalidIdentifier)
    }
}

""",
    )
    path.write_text("".join(lines), encoding="utf-8")


def update_tests() -> None:
    path = Path("tests/daily_portfolio_delivery_state.rs")
    lines = read_lines(path)
    generation_function = unique_index(
        lines,
        "fn generation_compare_and_set_prevents_replayed_transitions() {\n",
    )
    if generation_function == 0 or lines[generation_function - 1] != "#[test]\n":
        raise RuntimeError("generation regression lost its #[test] anchor")
    lines[generation_function - 1 : generation_function - 1] = """#[test]
fn failed_lease_claim_does_not_consume_a_fence() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");

    assert_eq!(
        state.acquire(&spec.run_key, "worker-a", u64::MAX, 1),
        Err(DeliveryStateError::CounterOverflow)
    );
    assert!(state.lease(&spec.run_key).is_none());

    let first_valid = state
        .acquire(&spec.run_key, "worker-b", 100, 20)
        .expect("first successful lease");
    assert_eq!(first_valid.fence, 1);
}

""".splitlines(keepends=True)

    token_assignment = unique_index(
        lines,
        '    other_token_shape.destination = "gho_abcdefghijklmnopqrstuvwxyz1234567890".to_owned();\n',
    )
    lines[token_assignment : token_assignment + 1] = [
        '    other_token_shape.destination = ["slack:gho", "_test-marker"].concat();\n'
    ]

    empty_recovery = unique_index(
        lines,
        "    let empty_recovery_identity = plan_spec(\n",
    )
    lines[empty_recovery:empty_recovery] = """    let mut oversized_identifier = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    oversized_identifier.destination = "x".repeat(257);
    assert_eq!(
        state.plan(oversized_identifier),
        Err(DeliveryStateError::InvalidIdentifier)
    );

""".splitlines(keepends=True)
    path.write_text("".join(lines), encoding="utf-8")


def update_workflow() -> None:
    path = Path(".github/workflows/daily-portfolio-delivery-state.yml")
    lines = read_lines(path)
    lines = replace_inclusive(
        lines,
        '              "crash-after-send",\n',
        '              "scheduled-baseline-isolation"\n',
        """              "crash-after-send",
              "expired-in-flight-recovery",
              "ambiguous-receipt-reconciliation",
              "exact-receipt-replay",
              "failure-atomic-fencing",
              "credential-shaped-identifier-rejection",
              "scheduled-baseline-isolation"
""",
    )
    path.write_text("".join(lines), encoding="utf-8")


def update_docs() -> None:
    path = Path("docs/daily-portfolio-delivery-state.md")
    text = path.read_text(encoding="utf-8")
    old_identifier = (
        "Identifiers are bounded ASCII-safe values and reject credential-shaped material. "
        "Digests are lowercase SHA-256 values.\n"
    )
    new_identifier = (
        "Identifiers are bounded before normalization, use ASCII-safe values, and reject "
        "credential-shaped material both as a complete identifier and after namespace "
        "separators such as `slack:`. Digests are lowercase SHA-256 values.\n"
    )
    old_fence = (
        "- Reacquisition after expiry advances the global fence, except an expired "
        "`delivering` run must first pass through explicit ambiguity recovery.\n"
    )
    new_fence = old_fence + (
        "- A failed claim computes its fence and expiry before mutation, so overflow or "
        "validation failure cannot consume a fencing value or leave a partial lease.\n"
    )
    if text.count(old_identifier) != 1 or text.count(old_fence) != 1:
        raise RuntimeError("documentation anchors changed")
    text = text.replace(old_identifier, new_identifier, 1)
    text = text.replace(old_fence, new_fence, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    update_source()
    update_tests()
    update_workflow()
    update_docs()
    for path in EXPECTED_FILES:
        if not path.is_file():
            raise RuntimeError(f"expected product file is missing: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
