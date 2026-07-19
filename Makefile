.DEFAULT_GOAL := build
CHECK_DIRS = pp_terminal tests

clean:
	rm -rf dist __pycache__ *.pyc *.pyo .poetry
	git submodule foreach --recursive git reset --hard

install: clean
	poetry install $(ARGS)
	patch -p1 < ./patch_ppxml2db.diff

check:
	poetry check
	poetry run pylint $(CHECK_DIRS)
	poetry run bandit -c bandit.yaml -r $(CHECK_DIRS)
	poetry run mypy .

test:
	poetry run coverage run --data-file=tests/.coverage -m pytest tests

test-mutations:
	poetry run mutmut run; status=$$?; poetry run mutmut results; exit $$status

build: install check test
	poetry build

inspect-mcp:
	cd tests/fixtures/ && npx @modelcontextprotocol/inspector -- poetry run pp-terminal --no-cache --config kommer.toml mcp $(ARGS)
