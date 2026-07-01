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
	@echo "  format            Run formatters (isort, black)"
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
	$(PYTHON) -m pip install --quiet isort black
	$(PYTHON) -m isort .
	$(PYTHON) -m black .

lint: venv
	$(PYTHON) -m pip install --quiet ruff mypy
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy src || true

test: venv
	$(PYTHON) -m pip install --quiet pytest
	$(PYTHON) -m pytest -q

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
	@set -euo pipefail; \
	# Determine version from pyproject.toml if not provided on the make command-line
	if [ -z "$(VERSION)" ]; then \
		VERSION=$$($(PY) -c "import tomllib,sys; print(tomllib.loads(open('pyproject.toml','rb').read())['project']['version'])"); \
	else \
		VERSION="$(VERSION)"; \
	fi; \
	# Ensure tag starts with 'v'
	TAG=$$VERSION; case "$$TAG" in v*) ;; *) TAG="v$$TAG";; esac; \
	echo "Releasing $$TAG"; \
	# Fetch tags so we can inspect remote state
	git fetch origin --tags >/dev/null 2>&1 || true; \
	# If tag exists (locally or on origin) recreate it at the same commit and push
	if git ls-remote --tags origin "refs/tags/$$TAG" | grep -q "$$TAG" || git rev-parse -q --verify "refs/tags/$$TAG" >/dev/null 2>&1; then \
		# If tag exists only on origin, fetch it locally so we can read its commit
		if ! git rev-parse -q --verify "refs/tags/$$TAG" >/dev/null 2>&1; then \
			git fetch origin "refs/tags/$$TAG:refs/tags/$$TAG" >/dev/null 2>&1 || true; \
		fi; \
		commit=$$(git rev-parse $$TAG); \
		echo "Tag $$TAG exists (commit $$commit) — recreating and pushing to origin"; \
		git push --delete origin $$TAG 2>/dev/null || true; \
		git tag -d $$TAG 2>/dev/null || true; \
		git tag -a $$TAG "$$commit" -m "Release $$TAG (recreated by make release)"; \
		git push origin $$TAG; \
	else \
		# Otherwise call the existing release script which will create and push the tag
		./scripts/release.sh $$TAG; \
	fi

update:
	sh ./scripts/update.sh

upload-pypi: build
	$(PYTHON) -m pip install --upgrade twine
	$(PYTHON) -m twine upload dist/*

clean:
	rm -rf build dist *.egg-info

distclean: clean
	rm -rf $(VENV_DIR) .pytest_cache .mypy_cache .ruff_cache
