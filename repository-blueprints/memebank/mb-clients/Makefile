PYTHON ?= python3
ARTIFACT_DIR ?= .artifacts
REPORT ?= $(ARTIFACT_DIR)/client-report.json

.PHONY: agent-check generate check-generated validate test rust-test typescript-test dart-test report clean

agent-check: check-generated validate test rust-test typescript-test dart-test

generate:
	$(PYTHON) scripts/generate_clients.py

check-generated:
	$(PYTHON) scripts/generate_clients.py
	git diff --exit-code -- clients/rust/src/generated.rs clients/dart/lib/src/generated.dart clients/typescript/src/generated.ts generated/manifest.json

validate:
	mkdir -p $(ARTIFACT_DIR)
	$(PYTHON) scripts/validate_clients.py --report $(REPORT)

test:
	$(PYTHON) -m unittest -v scripts/test_validate_clients.py

rust-test:
	cargo test --manifest-path clients/rust/Cargo.toml --locked

typescript-test:
	cd clients/typescript && npm run check
	cd clients/typescript && npm test

dart-test:
	cd clients/dart && dart pub get
	cd clients/dart && dart analyze
	cd clients/dart && dart run tool/smoke.dart

report: agent-check
	@cat $(REPORT)

clean:
	rm -rf $(ARTIFACT_DIR) scripts/__pycache__ clients/rust/target clients/dart/.dart_tool clients/dart/pubspec.lock
