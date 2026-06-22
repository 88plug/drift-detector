SHELL := /bin/bash
ROOT := /home/andrew/drift-detector

.PHONY: test selftest validate install eval eval-synth extract-real eval-real

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

eval-synth:
	python3 $(ROOT)/scripts/eval_morin.py | python3 -c \
	  "import json,sys; d=json.load(sys.stdin); \
	   print(json.dumps({'n':d['n'],'accuracy':d['accuracy'],'FP':d['false_positive_rate'],'FN':d['false_negative_rate']},indent=2))"

extract-real:
	python3 $(ROOT)/scripts/extract_real_corpus.py \
	  --out $(ROOT)/eval_real_corpus.json

eval-real: $(ROOT)/eval_real_corpus.json
	python3 $(ROOT)/scripts/backtest_real.py \
	  --calibrate --dump-disagreements | python3 -c \
	  "import json,sys; d=json.load(sys.stdin); \
	   print(json.dumps({'n':d['n'],'precision':d['precision'],'recall':d['recall'],'f1':d['f1'],'FP':d['false_positive_rate'],'FN':d['false_negative_rate'],'cm':d['confusion_matrix']},indent=2))"

eval: eval-synth eval-real
