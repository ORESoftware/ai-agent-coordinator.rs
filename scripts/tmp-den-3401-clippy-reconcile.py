#!/usr/bin/env python3
"""Apply the exact semantics-preserving DEN-3401 Clippy reconciliation."""

from pathlib import Path

path = Path("src/daily_portfolio_delivery_store.rs")
text = path.read_text(encoding="utf-8")
old = '''            if candidate_date > current_date {
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
new = '''            match candidate_date.cmp(&current_date) {
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
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected exactly one baseline ordering target, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
