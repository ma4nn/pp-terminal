"""
    Copyright (C) 2025-26 Dipl.-Ing. Christoph Massmann <chris@dev-investor.de>

    This file is part of pp-terminal.

    pp-terminal is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    pp-terminal is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with pp-terminal. If not, see <http://www.gnu.org/licenses/>.
"""

import typer

app = typer.Typer()

# Lines with a commented-out setting start with "#" directly (e.g. "#rate = 26.375"),
# explanatory prose starts with "# ". A test relies on this to uncomment every setting
# and validate the result against the config model, so keep the convention intact.
CONFIG_TEMPLATE = """\
# pp-terminal configuration
#
# Save this file as ~/.config/pp-terminal/config.toml (respecting $XDG_CONFIG_HOME)
# to have it loaded automatically, or pass it explicitly with --config <path>.
# CLI options always override the values set here.
#
# Every setting below is commented out and shows its default or an example value.
# Uncomment the lines you want to customise. Documentation:
# https://github.com/ma4nn/pp-terminal

# --- General ---

# Portfolio Performance file to analyse (saved as "XML with id attributes").
#file = "portfolio_performance.xml"

# Number of decimal places for numeric output.
#precision = 4

# Taxonomy used for asset-class grouping, by its name in Portfolio Performance.
#taxonomy = "Asset Allocation"

# --- Taxes ---

#[tax]
# Capital gains tax rate in percent (Abgeltungsteuer incl. Soli).
#rate = 26.375
# CSV file(s) with prepaid tax already paid (Vorabpauschale).
#files = ["taxes_paid.csv"]
# Partial exemption in percent (Teilfreistellung), e.g. 30 for equity funds.
#exemption-rate = 30.0
# Attribute UUID whose per-security value overrides exemption-rate.
#exemption-rate-attribute = "b3c38686-2d22-4b5d-8e38-e61dcf6fdde3"
# Annual tax-free allowance (Sparerpauschbetrag) in account currency.
#allowance = 1000.0

# --- View: accounts ---

# Columns to show; attribute UUIDs are allowed as field names.
#[commands.view.accounts]
#fields = ["AccountId", "Name", "Balance"]

# --- View: securities ---

#[commands.view.securities]
#fields = ["SecurityId", "WKN", "Name", "Shares", "Messages"]

# --- Simulate: pmt (withdrawal plan) ---

#[commands.simulate.pmt]
# Expected annual returns in percent to simulate.
#returns = [2, 4, 6]
# End date of the withdrawal horizon.
#end-date = 2055-12-31
# Per-asset-class expected returns in percent (keys are taxonomy class names, see "view taxonomies" command).
#returns-by-class = { "Eigenkapital" = 5.0, "Fremdkapital" = 1.9 }

# --- Simulate: share-sell ---

#[commands.simulate.share-sell]
# Minimum sale amount per position (must be > 0).
#min-amount = 500

# --- Validate: account rules ---
# One [[...rules]] block per rule. Types: balance-limit,
# balance-limit-from-attribute, date-passed-from-attribute, vap-liquidity.

#[[commands.validate.accounts.rules]]
#type = "balance-limit"
#value = 25000
# severity: "error" (default) or "warning".
#severity = "warning"
# applies-to: restrict the rule to specific account UUIDs (optional).
#applies-to = ["c9c57e01-7ea0-4e70-bed9-4656941f7687"]

# --- Validate: security rules ---
# Types: price-staleness, price-limit, price-limit-from-attribute,
# cost-basis-limit, cost-basis-limit-from-attribute, paid-tax-validation,
# negative-share-balance.

#[[commands.validate.securities.rules]]
#type = "price-staleness"
#severity = "warning"
#value = 30
# valid-months: restrict the rule to these calendar months, 1-12 (optional).
#valid-months = [3]
# tolerance: allowed slack before flagging (security rules only).
#tolerance = 0.0

# --- Anonymize ---
# Map attribute UUIDs to fake-data providers for --anonymize and export.
# The presence of this section alone enables anonymization.

#[anonymize.attributes."a1b2c3d4-e5f6-7890-abcd-ef1234567890"]
#provider = "iban"

#[anonymize.attributes."f9deb0dd-8bd7-47b1-ac3f-30fedd6a47e9"]
#provider = "pyfloat"
#args = { min_value = 0.0, max_value = 1.0, right_digits = 2 }
"""


@app.command(name="init")
def init_config() -> None:
    """
    Print a sample configuration file with every option commented out.

    Redirect it to create a config, e.g. `pp-terminal init > config.toml`.
    """

    typer.echo(CONFIG_TEMPLATE)
