SHELL := /bin/bash
ROOT := /home/andrew/drift-detector

.PHONY: test selftest validate install

test:
	cd $(ROOT) && python3 -m pytest tests/ -v

selftest:
	python3 $(ROOT)/src/lib/drift_score.py --selftest

validate:
	@if [ -f $(ROOT)/.ci/validate_plugin.py ]; then \
		python3 $(ROOT)/.ci/validate_plugin.py; \
	else \
		python3 $(ROOT)/src/lib/drift_score.py --selftest; \
	fi

install:
	bash $(ROOT)/install.sh
