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

from dataclasses import dataclass
from enum import Enum
from typing import Optional, TypeAlias, Any

import pandera.pandas as pa
from pandera.typing import DataFrame, Index, Series
from pydantic import BaseModel

Money: TypeAlias = float
Percent: TypeAlias = float


@dataclass(frozen=True)
class Attribute:
    uuid: str
    name: str
    converter: str
    label: str = ''

    @property
    def column(self) -> str:
        """Portfolio Performance's own table header, which is shorter than the descriptive name."""
        return self.label or self.name


@dataclass(frozen=True)
class Taxonomy:
    uuid: str
    name: str


class TransactionType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    DELIVERY_INBOUND = "DELIVERY_INBOUND"
    DELIVERY_OUTBOUND = "DELIVERY_OUTBOUND"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    DEPOSIT = "DEPOSIT"
    REMOVAL = "REMOVAL"
    INTEREST = "INTEREST"
    INTEREST_CHARGE = "INTEREST_CHARGE"
    FEES_REFUND = "FEES_REFUND"
    FEES = "FEES"
    DIVIDENDS = "DIVIDENDS"
    TAXES = "TAXES"
    TAX_REFUND = "TAX_REFUND"


class AccountType(Enum):
    SECURITIES = "portfolio"
    DEPOSIT = "account"


class _CoercingSchema(pa.DataFrameModel):
    """Base schema that coerces dtypes on validation; our DataFrames come from SQL/computation as object dtype."""

    class Config:  # pylint: disable=too-few-public-methods
        coerce = True


class TransactionSchema(_CoercingSchema):
    date: Index[pa.DateTime]
    accountId: Index[str]
    securityId: Index[str] = pa.Field(nullable=True)
    type: Series[str]  # @todo use pandera preprocessing?
    amount: Series[Money]
    shares: Series[float]
    accountType: Series[str]
    taxes: Series[Money] = pa.Field(default=0.0)
    fees: Optional[Series[Money]] = pa.Field(default=0.0, coerce=True)
    currency: Series[str] = pa.Field(nullable=True)
    transferTargetAccount: Optional[Series[str]] = pa.Field(nullable=True)  # destination securities account of a TRANSFER_OUT (from PP's cross-entry link)
    transferTargetShares: Optional[Series[float]] = pa.Field(nullable=True)  # shares booked by the paired TRANSFER_IN, normally identical to this row's shares


class AccountSchema(_CoercingSchema):
    accountId: Index[str]
    name: Series[str]
    type: Series[str]  # @todo use pandera preprocessing?
    referenceAccount: Optional[Series[str]] = pa.Field(nullable=True)
    isRetired: Optional[Series[bool]] = pa.Field(coerce=True)
    currency: Series[str] = pa.Field(nullable=True)


class Security(BaseModel):  # pylint: disable=too-few-public-methods
    securityId: str
    name: str
    wkn: str | None
    currency: str | None
    isRetired: Optional[bool] = pa.Field(coerce=True)
    additionalAttributes: dict[str, Any] = {}

class SecuritySchema(_CoercingSchema):
    securityId: Index[str]
    name: Series[str]
    wkn: Series[str] = pa.Field(nullable=True)
    isin: Optional[Series[str]] = pa.Field(nullable=True)
    currency: Series[str] = pa.Field(nullable=True)
    isRetired: Optional[Series[bool]] = pa.Field(coerce=True)


class SecurityPriceSchema(_CoercingSchema):
    date: Index[pa.DateTime]
    securityId: Index[str]
    price: Series[Money]


class TaxPaidSchema(_CoercingSchema):
    year: Index[int] = pa.Field(coerce=True)
    security_id: Index[str]
    deemed_income: Series[Money]


class TaxLotSchema(_CoercingSchema):
    date: Index[pa.DateTime]
    accountId: Index[str]
    securityId: Index[str]
    shares: Series[float]
    costBasis: Series[Money]
    purchasePrice: Series[Money] = pa.Field(nullable=True, coerce=True)
    currency: Series[str] = pa.Field(nullable=True)
    fees: Series[Money] = pa.Field(nullable=True, coerce=True)


class TaxLotSellSchema(TaxLotSchema):
    salePrice: Series[Money]
    exemptRate: Series[Percent]
    capitalGain: Series[Money]
    grossProceeds: Series[Money]
    deemedIncome: Series[Money]
    feePerShare: Series[Money]
    deemedIncomePerShare: Series[Money]
    netProceedsPerShare: Series[Money]
    taxableGain: Series[Money] = pa.Field(ge=0)
    totalTax: Series[Money]
    netProceeds: Series[Money]


class CashFlowResultSchema(_CoercingSchema):
    """Schema for cumulative external cash flow results, one row per currency."""
    currency: Series[str]
    totalDeposits: Series[Money]
    totalWithdrawals: Series[Money]
    netContributions: Series[Money]
    transactionCount: Series[int]


class InterestResultSchema(_CoercingSchema):
    """Schema for interest calculation results."""
    accountId: Index[str]
    name: Series[str]
    currency: Series[str]
    meanBalance: Series[Money]
    simulatedInterest: Series[Money]
    actualInterest: Series[Money] = pa.Field(nullable=True)

    @classmethod
    def empty(cls, *_args: Any) -> DataFrame['InterestResultSchema']:
        """Create empty DataFrame with correct index name.

        Overrides parent to fix Pandera limitation where .empty() doesn't preserve index names.
        """
        df = super().empty(*_args)
        df.index.name = 'accountId'
        return df


class VapResultSchema(_CoercingSchema):
    """Schema for Vorabpauschale (VAP) calculation results."""
    wkn: Series[str] = pa.Field(nullable=True)
    name: Series[str]
    currency: Series[str]

    class Config:  # pylint: disable=too-few-public-methods
        """Allow additional columns for dynamic account names."""
        strict = False  # Allow additional columns beyond those defined
