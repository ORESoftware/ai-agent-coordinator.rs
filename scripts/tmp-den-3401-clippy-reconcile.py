#!/usr/bin/env python3
"""Apply the exact semantics-preserving DEN-3401 Clippy reconciliations."""

from pathlib import Path

store_path = Path("src/daily_portfolio_delivery_store.rs")
store_text = store_path.read_text(encoding="utf-8")
old_ordering = '''            if candidate_date > current_date {
                true
            } else if candidate_date < current_date {
                false
            } else if current.baseline.plan_digest == run.spec.plan_digest
                && current.baseline.delivery_digest == run.spec.delivery_digest
                && current.baseline.receipt_id == receipt.receipt_id
            {
                false
            } else {
                return Err(domain(DeliveryStateError::BaselineConflict));
            }
'''
new_ordering = '''            match candidate_date.cmp(&current_date) {
                std::cmp::Ordering::Greater => true,
                std::cmp::Ordering::Less => false,
                std::cmp::Ordering::Equal
                    if current.baseline.plan_digest == run.spec.plan_digest
                        && current.baseline.delivery_digest == run.spec.delivery_digest
                        && current.baseline.receipt_id == receipt.receipt_id =>
                {
                    false
                }
                std::cmp::Ordering::Equal => {
                    return Err(domain(DeliveryStateError::BaselineConflict));
                }
            }
'''
ordering_count = store_text.count(old_ordering)
if ordering_count != 1:
    raise SystemExit(
        f"expected exactly one baseline ordering target, found {ordering_count}"
    )
store_path.write_text(
    store_text.replace(old_ordering, new_ordering, 1),
    encoding="utf-8",
)

adapter_path = Path("src/bin/prompt-reconciliation-adapter-policy.rs")
adapter_text = adapter_path.read_text(encoding="utf-8")
old_import = '''use std::{
    collections::BTreeSet,
    fmt,
    io::{self, Read},
};
'''
new_import = "use std::{collections::BTreeSet, fmt, io::Read};\n"
import_count = adapter_text.count(old_import)
if import_count != 1:
    raise SystemExit(
        f"expected exactly one prompt adapter io import target, found {import_count}"
    )
cursor_count = adapter_text.count("io::Cursor::new")
if cursor_count == 0:
    raise SystemExit("expected at least one test-only io::Cursor reference")
adapter_path.write_text(
    adapter_text.replace(old_import, new_import, 1).replace(
        "io::Cursor::new", "std::io::Cursor::new"
    ),
    encoding="utf-8",
)
