"""Common interface every chain implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..amounts import fmt


@dataclass
class Asset:
    """A sendable asset on a chain: the native coin or a token."""
    symbol: str
    decimals: int
    contract: str | None = None  # None = native coin


@dataclass
class PreparedTx:
    """A fully built and signed transaction, waiting for the user's confirmation."""
    chain: str
    asset: Asset
    sender: str
    recipient: str
    amount: int
    fee: int  # in native base units; an upper bound where the exact fee is unknown
    fee_note: str
    native: Asset
    _broadcast: Callable[[], str]
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Network:   {self.chain}",
            f"From:      {self.sender}",
            f"To:        {self.recipient}",
            f"Amount:    {fmt(self.amount, self.asset.decimals)} {self.asset.symbol}",
            f"Fee:       {fmt(self.fee, self.native.decimals)} {self.native.symbol} {self.fee_note}".rstrip(),
        ]
        if self.asset.contract is None:
            lines.append(f"Total:     {fmt(self.amount + self.fee, self.native.decimals)} {self.native.symbol}")
        else:
            lines.append(f"Token:     {self.asset.contract}")
        for w in self.warnings:
            lines.append(f"WARNING:   {w}")
        return "\n".join(lines)

    def broadcast(self) -> str:
        return self._broadcast()


@dataclass
class HistoryItem:
    txid: str
    time: int | None  # unix seconds; None while pending
    direction: str  # "in" | "out" | "self"
    amount: int  # base units of the asset, always >= 0
    counterparty: str = ""
    status: str = "confirmed"  # "confirmed" | "pending" | "failed"


class HistoryUnavailable(Exception):
    pass


class Chain:
    key: str = ""
    name: str = ""
    native: Asset
    explorer_tx: str = ""
    explorer_addr: str = ""

    def address(self) -> str:
        raise NotImplementedError

    def assets(self) -> list[Asset]:
        return [self.native]

    def balance(self, asset: Asset) -> int:
        raise NotImplementedError

    def validate_address(self, addr: str) -> str | None:
        """Return an error message, or None if the address is valid for this chain."""
        raise NotImplementedError

    def prepare_send(self, asset: Asset, to: str, amount: int | None) -> PreparedTx:
        """Build and sign. amount=None means "send everything" (where supported)."""
        raise NotImplementedError

    supports_max = False

    def history(self, asset: Asset, limit: int = 25) -> list[HistoryItem]:
        """Recent transactions, newest first."""
        raise HistoryUnavailable(f"History isn't available for {self.name}; use the explorer.")

    def tx_url(self, txid: str) -> str:
        return self.explorer_tx.format(txid)

    def address_url(self) -> str:
        return self.explorer_addr.format(self.address())

    def close(self) -> None:
        pass


class InsufficientFunds(Exception):
    pass
