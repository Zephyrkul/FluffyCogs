PYTHON ?= python3.8
DIFF := $(shell git diff --name-only --staged "*.py" "*.pyi")
ifeq ($(DIFF),)
	DIFF := $(shell git ls-files "*.py" "*.pyi")
endif

installreqs:
	$(PYTHON) -m pip install --upgrade flake8 autoflake isort black
lint:
	$(PYTHON) -m flake8 $(DIFF)
stylecheck:
	$(PYTHON) -m autoflake --check --imports aiohttp,discord,redbot $(DIFF)
	$(PYTHON) -m isort --check-only $(DIFF)
	$(PYTHON) -m black --check $(DIFF)
reformat:
	$(PYTHON) -m autoflake --in-place --imports=aiohttp,discord,redbot $(DIFF)
	$(PYTHON) -m isort $(DIFF)
	$(PYTHON) -m black $(DIFF)
