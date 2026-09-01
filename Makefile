.PHONY: help venv install-dev deps format lint test sdist wheel build build-standalone release update upload-pypi clean distclean

PY ?= python3
VENV_DIR ?= .venv
PYTHON = $(VENV_DIR)/bin/python

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Common targets:"
	@echo "  venv              Create virtualenv at $(VENV_DIR)"
	@echo "  install-dev       Install development dependencies (editable)"
	@echo "  deps              Install runtime dependencies (editable)"
	@echo "  format            Format the repository with Ruff"
	@echo "  lint              Run linters (ruff, mypy)"
	@echo "  test              Run test suite (pytest)"
	@echo "  sdist             Build source distribution (.tar.gz)"
	@echo "  wheel             Build wheel (.whl)"
	@echo "  build             Build sdist and wheel (into dist/)"
	@echo "  build-standalone  Build standalone app via scripts/build_standalone.py"
	@echo "  release VERSION=x.y.z  Run release script (scripts/release.sh)"
	@echo "  update            Build an update package (scripts/update.sh)"
	@echo "  upload-pypi       Upload built packages to PyPI (twine)"
	@echo "  clean             Remove build artifacts"
	@echo "  distclean         Remove build artifacts and $(VENV_DIR)"

venv:
	python3 -m venv $(VENV_DIR)
	$(PYTHON) -m pip install --upgrade pip setuptools wheel

install-dev: venv
	$(PYTHON) -m pip install -e .[dev]

deps: venv
	$(PYTHON) -m pip install -e .

format: venv
	$(PYTHON) -m pip install --quiet ruff
	$(PYTHON) -m ruff format .

lint: venv
	$(PYTHON) -m pip install --quiet ruff mypy
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy src

test: venv
	$(PYTHON) -m pip install --quiet pytest
	@$(PYTHON) -m pytest --collect-only -q | awk '/^tests\// {print $$0}' | while IFS= read -r test_node; do \
		QT_QPA_PLATFORM=offscreen $(PYTHON) -m pytest -q "$$test_node" || exit $$?; \
	done

sdist: venv
	$(PYTHON) -m pip install --upgrade build
	$(PYTHON) -m build --sdist -o dist

wheel: venv
	$(PYTHON) -m pip install --upgrade build
	$(PYTHON) -m build --wheel -o dist

build: sdist wheel

build-standalone: venv
	$(PYTHON) scripts/build_standalone.py

release:
	@PY=$(PY) ./scripts/make_release.sh $(VERSION)

update:
	sh ./scripts/update.sh

upload-pypi: build
	$(PYTHON) -m pip install --upgrade twine
	$(PYTHON) -m twine upload dist/*

clean:
	rm -rf build dist *.egg-info

distclean: clean
	rm -rf $(VENV_DIR) .pytest_cache .mypy_cache .ruff_cache
