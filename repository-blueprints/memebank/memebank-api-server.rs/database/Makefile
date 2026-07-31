PYTHON ?= python3
ARTIFACT_DIR ?= .artifacts
SCHEMA_SQL := $(ARTIFACT_DIR)/memebank-schema.sql
REPORT_JSON := $(ARTIFACT_DIR)/schema-report.json

.PHONY: agent-check validate test render report apply-dev verify-dev clean

agent-check: validate test render

validate:
	$(PYTHON) scripts/validate_schema.py

test:
	$(PYTHON) -m unittest -v scripts/test_validate_schema.py

render:
	mkdir -p $(ARTIFACT_DIR)
	$(PYTHON) scripts/render_schema.py \
		--output $(SCHEMA_SQL) \
		--report $(REPORT_JSON)

report: agent-check
	@cat $(REPORT_JSON)

apply-dev: agent-check
	@test -n "$(PGDATABASE)" || (echo "PGDATABASE is required" >&2; exit 2)
	psql -X -v ON_ERROR_STOP=1 -f bootstrap/roles.sql
	psql -X -v ON_ERROR_STOP=1 -f $(SCHEMA_SQL)
	psql -X -v ON_ERROR_STOP=1 -f tests/verify_schema.sql
	psql -X -v ON_ERROR_STOP=1 -f seeds/representative.sql
	psql -X -v ON_ERROR_STOP=1 -f tests/rls.sql

verify-dev:
	@test -n "$(PGDATABASE)" || (echo "PGDATABASE is required" >&2; exit 2)
	psql -X -v ON_ERROR_STOP=1 -f tests/verify_schema.sql
	psql -X -v ON_ERROR_STOP=1 -f tests/rls.sql

clean:
	rm -rf $(ARTIFACT_DIR) scripts/__pycache__
