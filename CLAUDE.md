# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pp-terminal is a command-line analytics tool for Portfolio Performance (https://www.portfolio-performance.info/).  
It reads Portfolio Performance XML files and provides analytical commands for portfolio insights, particularly German tax calculations like Vorabpauschale.

The tool uses ppxml2db (included as git submodule in ppxml2db/) to convert Portfolio Performance XML to SQLite, then loads the data into pandas DataFrames for analysis.

In this repo, after every code edit you make, run `make check` and at least any command like `pp-terminal --no-cache --output json --config config_kommer.toml view accounts` before proposing final changes.

## Important Implementation Rules

### General

- The original Portfolio Performance XML file may NEVER be written to
- pylint code quality MUST be 10/10
- If config format changes, adapt the Pydantic config models (in `utils/config.py` and the command modules), the `CONFIG_TEMPLATE` annotated reference in `commands/init.py` (a test validates it against the models), and `README.md` accordingly
- Never use `pylint: disable=protected-access` in tests
- Attention to properly use the correct financial terms for naming variables/methods/classes/files 
- Do NOT care about backwards compatibility unless explicitly stated (just leave a notice)
- AVOID unnecessary comments (including method descriptions) at best

### DataFrame Handling

- Prefer using DataFrames instead of iterating over arrays and custom classes for performance
- Prefer returning empty DataFrames as function results instead of `None`
- Pandera scheme models for DataFrames SHOULD be used for type safety and validation
- Column names e.g. for DataFrames MUST be camelCase to be in line with Portfolio Performance XML

## Folder Structure

The codebase follows a layered architecture for clarity and maintainability:

```
pp_terminal/
├── main.py                      # CLI entry point
├── exceptions.py                # Global exceptions
│
├── domain/                      # Core business logic (no external dependencies)
├── data/                        # Data loading & transformation
├── commands/                    # CLI commands (use cases)
├── output/                      # Output formatting strategies
├── validation/                  # Business validation rules
└── utils/                       # Framework utilities & config
```

Additional top-level modules (e.g. an MCP server) may live alongside `main.py`; they are entry points, not layers.

### Architectural Principles

- **Layer dependencies flow inward**: Commands → Output/Data → Domain
- **Domain layer is pure**: No dependencies on other layers
- **Use absolute imports**: `from pp_terminal.domain.portfolio import Portfolio` (makes architecture explicit)
- **Commands are top-level**: Primary features visible at a glance

## Development Commands

### Setup and Build
```bash
make                    # Full build: install, check, test, build
make install            # Install dependencies via uv and apply ppxml2db patch
make build              # Install dependencies, then build the distribution (no checks/tests)
make clean              # Remove build artifacts
```

The install step applies a patch (`patch_ppxml2db.diff`) to the ppxml2db submodule (the module itself is still under heavy development).  
To update the ppxml2db submodule run:
```bash
git submodule update --remote
```

After updating, re-check which tags ppxml2db reads an `id` attribute from and keep `ID_TAGS`
(`data/xml_id_reference_converter.py`) in sync — a tag missing there makes those entities vanish from the
database without any error. Most of them are not covered by the fixtures, so only `PPXML2DB_ID_TAGS` in
`tests/data/test_xml_id_reference_converter.py` guards this.

### Testing
```bash
make test               # Run pytest test suite
uv run pytest           # Run pytest directly
```

Test fixtures are in `tests/fixtures/` with sample Portfolio Performance XML files.

### Code Quality
```bash
make check              # Run all checks: uv lock --check, pylint, bandit, mypy
uv run pylint pp_terminal tests
uv run bandit -c bandit.yaml -r pp_terminal tests
uv run mypy .
```

### Running Locally
```bash
uv run pp-terminal --version
uv run pp-terminal --file=depot.xml view accounts
uv run pp-terminal --file=depot.xml --verbose view accounts  # or --debug, a synonym
```

`--verbose`/`--debug` enable debug logging and let the original exception surface instead of a plain abort.
`--cache` (the default) writes a `.<xml-stem>.<checksum>.pp-terminal.db` sqlite file next to the xml.

## Architecture

### Data Flow
1. **XML → SQLite**: `Ppxml2dbWrapper` uses ppxml2db to parse Portfolio Performance XML into SQLite (in-memory or cached)
2. **SQLite → DataFrames**: `PpPortfolioBuilder` reads from SQLite into pandas DataFrames with Pandera schemas
3. **DataFrames → Portfolio**: Creates validated `Portfolio` object containing accounts, transactions, securities, prices
4. **Portfolio → Snapshot**: `PortfolioSnapshot` filters portfolio data by date, calculates shares/balances
5. **Snapshot → Output**: Commands generate results, `OutputStrategy` formats as TABLE/JSON/CSV

### Core Abstractions

**Portfolio** (`domain/portfolio.py`): Container for all portfolio data
- Properties separate securities vs deposit accounts and their transactions
- All data stored as typed pandas DataFrames with Pandera schemas
- Validates data against schemas on construction

**PortfolioSnapshot** (`domain/portfolio_snapshot.py`): Time-based portfolio view
- Filters transactions/prices up to a specific date
- Calculates derived values: shares, balances, latest prices
- Used by commands to generate point-in-time reports

**PpPortfolioBuilder** (`data/pp_portfolio_builder.py`): Constructs Portfolio from XML
- Uses Ppxml2dbWrapper to load XML into SQLite
- Executes SQL queries to extract accounts/transactions/securities/prices
- Applies scaling factors (amounts in cents, shares scaled by 100000000)
- Negates amounts for outbound transaction types

**Schemas** (`domain/schemas.py`): Pandera schemas and enums for DataFrame validation
- One schema per core entity (transactions, accounts, securities, prices, …)
- DataFrames are typed as `DataFrame[SchemaType]` and validated on Portfolio construction
- Domain-level enums (transaction types, account types, …) live here too

### Plugin System

Commands are loaded via Python entry points under the `pp_terminal.commands` group in `pyproject.toml`. The entry point name encodes the command group: `"<group>.<name>" = "pp_terminal.commands.<module>:app"` registers `<name>` inside the `<group>` Typer app; a bare `"<name>"` registers a top-level command.

`utils/plugins.py` discovers and loads these at startup; `main.py` declares the available groups via `app.add_typer(...)`. To add a command, create the module under `pp_terminal/commands/` and add one entry-point line — no changes elsewhere.

### Command Structure

Commands use Typer and follow this pattern (see any module under `pp_terminal/commands/`):
- Accept `ctx: typer.Context` to access `ctx.obj.portfolio` and `ctx.obj.output`
- Build analysis using Portfolio/PortfolioSnapshot
- Format output via OutputStrategy (TABLE/JSON/CSV/…)
- Use `Console` from Rich for printing

Main app structure (`main.py`):
- Registers command groups via `app.add_typer(...)` (see the calls near the top of the file for the current set)
- Loads external plugins into groups
- Main callback parses XML file and creates Portfolio in `ctx.obj`

### Portfolio Performance XML Input

The XML format used by Portfolio Performance is nothing but internal serialization format of 3rd-party library [XStream](https://x-stream.github.io/). 

Both XStream reference flavors are accepted: files saved as "XML with id attributes" go straight to ppxml2db, while the default "XML" flavor (relative path references) is normalized in memory by `data/xml_id_reference_converter.py` first.

### Output System

`pp_terminal/output/` implements the strategy pattern: one strategy class per supported format, selected via factory. Add a new format by adding a strategy module and registering it in the factory.

## Key Patterns

### Transaction Amount Signs
`PpPortfolioBuilder` normalizes amounts to signed values during XML→DataFrame conversion: outbound transaction types are negated, inbound stay positive. The exact per-account-type mapping lives in `PpPortfolioBuilder` — treat it as the source of truth rather than duplicating here.

### Scaling Factors
The Portfolio Performance XML stores amounts as cents and shares/prices scaled by `10^8`. `PpPortfolioBuilder` applies the inverse scaling when loading; downstream code works in human units.

### DataFrame Filtering
`data/filters.py` collects reusable DataFrame operations (date/type/security/account filters, currency unstacking, taxonomy pivots, …). Prefer extending this module over inlining ad-hoc filtering in commands.

### Testing
`tests/conftest.py` patches the ppxml2db database to in-memory SQLite and exposes shared fixture DataFrames. Test against behavior, not implementation details, and reuse fixtures.

## Configuration

- Python >=3.13 required
- Uses uv for dependency management
- Main dependencies: typer, pandas, rich, pandera, lxml
- Dev dependencies: pylint, pytest, mypy, bandit

## Licensing
GPL-3.0-or-later. All source files include copyright header.

## Resources

- https://www.portfolio-performance.info/
- https://github.com/pfalcon/ppxml2db
- https://www.gesetze-im-internet.de/invstg_2018/__18.html