PYTHON ?= python3
VISION_ROOT := benchmarks/vision
REPORT ?= .artifacts/vision-benchmark-report.json

.PHONY: agent-check vision-check report clean

agent-check: vision-check

vision-check:
	$(PYTHON) -m py_compile \
		$(VISION_ROOT)/scripts/evaluate.py \
		$(VISION_ROOT)/tests/test_evaluate.py
	$(PYTHON) $(VISION_ROOT)/tests/test_evaluate.py -v
	mkdir -p .artifacts
	$(PYTHON) $(VISION_ROOT)/scripts/evaluate.py \
		--root $(VISION_ROOT) \
		--report $(REPORT)

report: vision-check

clean:
	rm -rf .artifacts benchmarks/vision/scripts/__pycache__ benchmarks/vision/tests/__pycache__
