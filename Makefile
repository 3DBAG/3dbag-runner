.PHONY: help install install-dev test test-cov check format clean docker-build generate-workflows

# Variables
PYTHON := uv run python
UV := uv
DOCKER_IMAGE := 3dbag-runner
DOCKER_TAG := latest
SRC_DIR := src
TEST_DIR := tests

# Default target
help:
	@echo "Available targets:"
	@echo "  install          Install dependencies using uv"
	@echo "  install-dev      Install development dependencies"
	@echo "  test             Run tests with pytest"
	@echo "  test-cov         Run tests with coverage report"
	@echo "  check            Run all code quality checks (lint, type-check, format-check)"
	@echo "  format           Auto-format code with autoflake and autopep8"
	@echo "  clean            Clean up cache files and build artifacts"
	@echo "  docker-build     Build Docker image"
	@echo "  generate-workflows Generate all Argo workflow YAML files"

# Package management
install:
	$(UV) sync

install-dev:
	$(UV) sync --group dev

# Testing
test:
	PYTHONPATH=$(SRC_DIR) $(UV) run pytest $(TEST_DIR) -v

test-cov:
	PYTHONPATH=$(SRC_DIR) $(UV) run pytest $(TEST_DIR) --cov=$(SRC_DIR) --cov-report=html --cov-report=term-missing --cov-report=xml -v

test-azure:
	PYTHONPATH=$(SRC_DIR) $(UV) run pytest $(TEST_DIR) -m azure -v

test-no-azure:
	PYTHONPATH=$(SRC_DIR) $(UV) run pytest $(TEST_DIR) -m "not azure" -v

# Code quality
check:
	@echo "Run autoflake"
	$(UV) run autoflake --remove-all-unused-imports --recursive --remove-unused-variables --check $(SRC_DIR) $(TEST_DIR)

	@echo "Run autopep8"
	$(UV) run autopep8 --diff --max-line-length=120 --recursive $(SRC_DIR) $(TEST_DIR)

	@echo "Run flake8"
	$(UV) run flake8 $(SRC_DIR) $(TEST_DIR)

	@echo "Run mypy"
	$(UV) run mypy $(SRC_DIR)

format:
	$(UV) run autoflake --remove-all-unused-imports --recursive --remove-unused-variables --in-place $(SRC_DIR) $(TEST_DIR)
	$(UV) run autopep8 --in-place --aggressive --aggressive --max-line-length=120 --recursive $(SRC_DIR) $(TEST_DIR)

# Docker
docker-build:
	docker build -f docker/Dockerfile -t $(DOCKER_IMAGE):$(DOCKER_TAG) .

# Cleanup
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf coverage.xml
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf dist/
	rm -rf build/
	rm -rf tempdir/output/
	rm -rf generated/*.yaml

# Combined targets
dev-setup: install-dev
	@echo "Development environment setup complete!"
	@echo "Run 'make help' to see available commands"

# Generate Argo workflows
generate-workflows:
	$(PYTHON) $(SRC_DIR)/argo/createbagdb.py
	$(PYTHON) $(SRC_DIR)/argo/dump.py
	$(PYTHON) $(SRC_DIR)/argo/fixcityjson.py
	$(PYTHON) $(SRC_DIR)/argo/geluid.py
	$(PYTHON) $(SRC_DIR)/argo/height.py
	$(PYTHON) $(SRC_DIR)/argo/lazdb.py
	$(PYTHON) $(SRC_DIR)/argo/lazsplit.py
	$(PYTHON) $(SRC_DIR)/argo/pdokupdatebuildings.py
	$(PYTHON) $(SRC_DIR)/argo/pdokupdategeluid.py
	$(PYTHON) $(SRC_DIR)/argo/roofer.py
	$(PYTHON) $(SRC_DIR)/argo/tyler.py
	$(PYTHON) $(SRC_DIR)/argo/validatecityjson.py
	$(PYTHON) $(SRC_DIR)/remove_buildings.py
