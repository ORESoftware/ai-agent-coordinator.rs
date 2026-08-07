PYTHON ?= python3
ARTIFACT_DIR ?= .artifacts
REPORT ?= $(ARTIFACT_DIR)/infra-report.json
RENDER_DIR ?= $(ARTIFACT_DIR)/rendered

.PHONY: agent-check validate test render report clean

agent-check: validate test render

validate:
	mkdir -p $(ARTIFACT_DIR)
	$(PYTHON) scripts/validate_infra.py --report $(REPORT)

test:
	$(PYTHON) -m unittest -v scripts/test_validate_infra.py

render:
	$(PYTHON) scripts/render_infra.py --output $(RENDER_DIR)

report: agent-check
	@cat $(REPORT)

clean:
	rm -rf $(ARTIFACT_DIR) scripts/__pycache__
