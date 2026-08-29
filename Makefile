.DEFAULT_GOAL := all
CHECK_DIRS = pp_terminal tests

all: install check test build

clean:
	rm -rf dist __pycache__ *.pyc *.pyo
	git submodule foreach --recursive git reset --hard

install: clean
	uv sync $(ARGS)
	patch -p1 < ./patch_ppxml2db.diff

check:
	uv lock --check
	uv run pylint $(CHECK_DIRS)
	uv run bandit -c bandit.yaml -r $(CHECK_DIRS)
	uv run mypy .

test:
	uv run coverage run --data-file=tests/.coverage -m pytest tests

test-mutations:
	uv run mutmut run; status=$$?; uv run mutmut results; exit $$status

build: install
	uv build

inspect-mcp:
	cd tests/fixtures/ && npx @modelcontextprotocol/inspector -- uv run pp-terminal --no-cache --config kommer.toml mcp $(ARGS)
