"""
cmp_server_pocket_option_2026.models
=======================

Typed models for the data PocketOption sends and receives. Field shapes here
were verified against real captured traffic (see the project's dump), not
guessed. Validation is deliberately lenient (``extra="allow"``) so an added
server field never crashes parsing — we simply keep it.
"""
from __future__ import annotations

import enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Direction(str, enum.Enum):
    """Trade direction. The value is exactly what the server's ``action`` wants."""

    CALL = "call"   # up   / higher  -> server command 0
    PUT = "put"     # down / lower   -> server command 1

    @classmethod
    def coerce(cls, value: "Direction | str | int") -> "Direction":
        """Accept many spellings: 'call'/'buy'/'up'/0, 'put'/'sell'/'down'/1, ..."""
        if isinstance(value, Direction):
            return value
        if isinstance(value, bool):  # guard: bool is an int subclass
            raise ValueError("direction must not be a bool")
        if isinstance(value, int):
            return cls.CALL if value == 0 else cls.PUT
        key = str(value).strip().lower()
        mapping = {
            "call": cls.CALL, "buy": cls.CALL, "up": cls.CALL, "higher": cls.CALL, "0": cls.CALL,
            "put": cls.PUT, "sell": cls.PUT, "down": cls.PUT, "lower": cls.PUT, "1": cls.PUT,
        }
        if key not in mapping:
            raise ValueError(
                f"Invalid direction {value!r} — use 'call'/'up' or 'put'/'down'"
            )
        return mapping[key]

    @property
    def command(self) -> int:
        """The numeric form the server echoes back in a Deal (0=call, 1=put)."""
        return 0 if self is Direction.CALL else 1


class Balance(BaseModel):
    """Account balance snapshot (from ``successupdateBalance``)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    balance: float
    is_demo: bool = Field(True, alias="isDemo")


class Deal(BaseModel):
    """A single trade, open or closed (from ``successopenOrder`` / a ``deals[]`` item)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    asset: str
    amount: float
    command: int                                        # 0=call/up, 1=put/down
    profit: float = 0.0                                 # >0 win, <0 loss (== -amount)
    percent_profit: int = Field(0, alias="percentProfit")
    percent_loss: int = Field(0, alias="percentLoss")
    open_price: float = Field(0.0, alias="openPrice")
    close_price: float = Field(0.0, alias="closePrice")
    open_timestamp: int = Field(0, alias="openTimestamp")   # unix — source of truth
    close_timestamp: int = Field(0, alias="closeTimestamp")
    request_id: int = Field(0, alias="requestId")           # correlates open<->result
    option_type: int = Field(100, alias="optionType")
    currency: str = "USD"
    is_demo: bool = Field(True, alias="isDemo")

    @property
    def direction(self) -> Direction:
        return Direction.CALL if self.command == 0 else Direction.PUT

    @property
    def is_closed(self) -> bool:
        return self.close_price != 0

    @property
    def won(self) -> Optional[bool]:
        """True/False once closed, None while still open."""
        if not self.is_closed:
            return None
        return self.profit > 0


# Positional indices within an updateAssets row (verified from captured data).
_ASSET_ID = 0
_ASSET_SYMBOL = 1
_ASSET_NAME = 2
_ASSET_TYPE = 3
_ASSET_PAYOUT = 5
_ASSET_ACTIVE = 14
_ASSET_EXPIRATIONS = 15


class Candle(BaseModel):
    """One OHLCV candle (a row of the ``candles`` array in a history payload).

    Verified row layout: ``[time, open, close, high, low, volume]`` — ``time`` is
    the unix start of the candle and consecutive candles chain (each candle's
    open equals the previous candle's close).
    """

    model_config = ConfigDict(extra="allow")

    time: int
    open: float
    close: float
    high: float
    low: float
    volume: float = 0.0

    @classmethod
    def from_row(cls, row: list) -> "Candle":
        return cls(
            time=int(row[0]),
            open=float(row[1]),
            close=float(row[2]),
            high=float(row[3]),
            low=float(row[4]),
            volume=float(row[5]) if len(row) > 5 else 0.0,
        )


class Asset(BaseModel):
    """A tradeable instrument (one row of ``updateAssets``).

    ``updateAssets`` rows are positional arrays. The indices in ``_ASSET_*``
    (module level) were verified from captured data; extra positions are ignored.
    If PocketOption reorders the array, those constants are the one place to fix.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    symbol: str
    name: str
    type: str                    # "currency" | "stock" | "crypto" | "commodity" | ...
    payout: int                  # percent, e.g. 92
    is_active: bool              # open for trading right now
    expirations: List[int]       # allowed durations in seconds, e.g. [60, 120, 300, ...]

    @classmethod
    def from_row(cls, row: list) -> "Asset":
        def cell(index: int, default=None):
            return row[index] if isinstance(row, list) and len(row) > index else default

        raw_exp = cell(_ASSET_EXPIRATIONS, []) or []
        expirations = sorted(
            {e["time"] for e in raw_exp if isinstance(e, dict) and "time" in e}
        )
        return cls(
            id=cell(_ASSET_ID, 0) or 0,
            symbol=cell(_ASSET_SYMBOL, "") or "",
            name=cell(_ASSET_NAME, "") or "",
            type=cell(_ASSET_TYPE, "") or "",
            payout=cell(_ASSET_PAYOUT, 0) or 0,
            is_active=bool(cell(_ASSET_ACTIVE, False)),
            expirations=expirations,
        )

    @property
    def is_otc(self) -> bool:
        return self.symbol.endswith("_otc")
