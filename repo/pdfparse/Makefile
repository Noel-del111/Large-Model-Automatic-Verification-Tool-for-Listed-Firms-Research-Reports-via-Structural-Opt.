PY ?= python
SRC := src
INPUT ?= data/raw
OUT ?= data/out

export PYTHONPATH := $(SRC)

.PHONY: doctor parse verify report test sample

doctor:
	$(PY) -m yjparse doctor

parse:
	$(PY) -m yjparse parse --input $(INPUT) --out $(OUT)

verify:
	$(PY) -m yjparse verify --out $(OUT)

report:
	$(PY) -m yjparse report --out $(OUT)

test:
	$(PY) -m unittest discover -s tests -v

sample:
	$(PY) -c "from pathlib import Path; import sys; sys.path.insert(0,'tests'); from sample_pdfs import make_all; print(make_all(Path('data/raw')))"
