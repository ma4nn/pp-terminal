# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Support for Portfolio Performance files saved as plain "XML" (without id attributes)
- Support for the `BooleanConverter` attribute type
- Start capital the withdrawal is based on in `simulate pmt`
- `--debug` as a synonym for `--verbose`
- Debug logging of the config and Portfolio Performance file paths actually used

### Changed

- Attribute columns are labelled with Portfolio Performance's column label instead of the longer attribute name
  (e.g. `TER` instead of `Gesamtkostenquote (TER)`), which is also what `--fields` now matches

### Fixed

- `view accounts --fields` accepts an account attribute by uuid, not just by name
- Errors raised by a command (e.g. a mistyped `--fields` value) print a message instead of a stack trace
- Columns requested via `--fields` or the config file are no longer dropped from the table when they hold no values
- Currency columns in `view accounts` no longer appear in a random order

## [0.11.0] - 2026-07-09

### Added 

- Claude Code GitHub Workflow
- Added possibility for plugins to define config toml
- Validation for negative shares

### Changed

- Updated dependencies

## [0.10.1] - 2026-04-14

### Fixed 

- Handle null property values in PP XML v69+

### Changed

- Updated dependencies

## [0.10.0] - 2026-02-11

### Added 

- MCP server via `mcp` command
- Portfolio Performance taxonomies are now available in `view securities` (cli) and `query_securities` (mcp)

### Changed

- Updated dependencies

## [0.9.0] - 2026-02-09

### Added

- Parse & validate paid taxes from CSV file
- Total VAP field and total row to `view securities`
- Make `simulate share-sell` support simulation of whole portfolio sell
- `--target-net` option to `simulate share-sell` to optimize sell for certain target net proceed
- `tolerance` option for validation rules
- `anonymize` flag in configuration file

### Changed

- Configuration file can now also be provided by environment variable `PP_TERMINAL_CONFIG` for easier handling
- Renamed `purchase-cost-limit` to `cost-basis-limit`
- Renamed `exemption_rate` to `exempt_rate`
- Renamed `--format` to `--output`
- Renamed `--columns` to `--fields
- `export` command now uses an argument for the output file
- Use Portfolio Performance converter classes for formatting

## [0.8.0] - 2026-02-06

### Added

- New validation rule for `validate` command to check for sufficient account balance for Vorabpauschale (VAP)
- Excel output format
- `--anonymize` is now a global flag instead of a subcommand so that it can be applied to every command

### Changed

- Renamed `simulate vorabpauschale` to shorter `simulate vap`
- Updated dependencies

## [0.7.0] - 2026-01-26

### Added

- New `export anonymized` command to create an anonymized version of the Portfolio Performance XML file

### Changed

- Switched from JSON to TOML configuration format to improve readability and to add comments
- Renamed `list` commands to more intuitive `view`
- Unified all column names to camelCase to align with Portfolio Performance
- Improved internal folder structure
- Updated dependencies

## [0.6.1] - 2026-01-11

### Fixed

- Issue with missing json schema in final build

## [0.6.0] - 2026-01-11

### Added 

- New `simulate share-sell` command
- New `list securities` command
- Parameters can now also be set via config json file for easier handling
- Support for `PercentPlainConverter` and `PercentConverter` for exempt rate defined in Portfolio Performance
- Added more tests

### Changed

- Colorized related account balance for Vorabpauschale simulations
- Updated dependencies

## [0.5.4] - 2025-07-30

### Changed

- Updated dependencies

## [0.5.2] - 2025-02-04

### Fixed

- Issue with "nan" being displayed in result table in case of missing locale

### Changed

- switched cache database file suffix to `.db` for `--debug`

## [0.5.1] - 2025-02-03

### Fixed

- `simulate vorabpauschale` command: per-security exemption rate was not taken into account

### Changed

- Interest rate option for simulate interest is now similar to the other commands
- Made "no results" message nicer

## [0.5.0] - 2025-02-03

### Added

- New command `simulate interest`
- Internal database is saved if `--debug` option is present

## [0.4.0] - 2025-02-02

### Fixed

- Issue if locale is not set

### Changed

- Commands for deposit and securities accounts are now grouped together and can be filtered via `--type` option
- Renamed `view` command to `list`
- Updated ppxml2db from 1.7 to 1.7.1

## [0.3.0] - 2025-01-29

### Added

- Multi-currency support
- Support for CSV and JSON output formats via `--format`

## [0.2.0] - 2025-01-27

### Changed

- Converted Portfolio Performance file argument to an option (`--file`)
- Renamed "depot" to "securities account" to align with Portfolio Performance terms and to make the purpose clearer
- Some readme clarifications

## [0.1.0] - 2025-01-25

Initial Release 🥳
