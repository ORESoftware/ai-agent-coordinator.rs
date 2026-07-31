PYTHON ?= python3
REPORT ?= .artifacts/contract-report.json

.PHONY: agent-check validate test report clean

agent-check: validate test

validate:
	$(PYTHON) scripts/validate_contracts.py

test:
	$(PYTHON) -m unittest -v scripts/test_validate_contracts.py

report:
	mkdir -p $(dir $(REPORT))
	$(PYTHON) scripts/validate_contracts.py --report $(REPORT)

clean:
	rm -rf .artifacts __pycache__ scripts/__pycache__
