"""Usage, cost and balance reports for Chat accounts.

A report answers what one account holds: credits used, what is left, and what
the balance is, when the provider says so. Unknown values stay unknown --
nothing here estimates a bill. Adapters are small and per-provider; a failure
becomes a report with an error note, never zeros.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from accessible_ai.models import Account
from accessible_ai.storage.credentials import CredentialStore


@dataclass
class UsageReport:
    """One account's usage state, as far as its provider reports it."""

    provider: str = ""
    account_name: str = ""
    checked_at: str = ""
    usage: float | None = None  # Spent/requests count as the provider names it.
    limit_remaining: float | None = None
    balance: float | None = None
    is_free_tier: bool | None = None
    note: str = ""
    error: str = ""


def _get_json(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else {}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def openrouter_report(account: Account, credentials: CredentialStore) -> UsageReport:
    """OpenRouter's key and credit endpoints, verified against its docs."""
    report = UsageReport(
        provider="openrouter",
        account_name=account.name,
        checked_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    key = credentials.get_api_key(int(account.id)).strip() if account.id is not None else ""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    base = account.base_url.rstrip("/") + "/"
    try:
        current = _get_json(urljoin(base, "key"), headers, float(account.timeout_seconds))
    except httpx.HTTPError as exc:
        report.error = f"Could not check OpenRouter usage: {exc}"
        return report
    data = current.get("data") if isinstance(current.get("data"), dict) else {}
    report.usage = _number(data.get("usage"))
    report.limit_remaining = _number(data.get("limit_remaining"))
    if "is_free_tier" in data:
        report.is_free_tier = bool(data["is_free_tier"])
    try:
        credit_payload = _get_json(
            urljoin(base, "credits"), headers, float(account.timeout_seconds)
        )
        report.balance = _number(credit_payload.get("data"))
    except httpx.HTTPError:
        report.note = "Balance endpoint unavailable; usage above still applies."
    return report


def ollama_report(account: Account, credentials: CredentialStore) -> UsageReport:
    """Ollama keeps models locally and reports no usage or balance."""
    return UsageReport(
        provider="ollama",
        account_name=account.name,
        checked_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        note="Ollama runs locally, so there is no usage or balance to report.",
    )


_ADAPTERS = {
    "openrouter": openrouter_report,
    "ollama": ollama_report,
}


def usage_report(account: Account, credentials: CredentialStore) -> UsageReport:
    """The report for one account, or an honest 'not reported' note."""
    adapter = _ADAPTERS.get(account.provider)
    if adapter is None:
        return UsageReport(
            provider=account.provider,
            account_name=account.name,
            checked_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            note=f"This provider does not publish usage for {account.name}.",
        )
    return adapter(account, credentials)


def format_report(report: UsageReport) -> str:
    """One readable block for a dialog or an announcement."""
    lines = [
        f"Account: {report.account_name}",
        f"Provider: {report.provider}",
        f"Checked: {report.checked_at}",
    ]
    if report.error:
        lines.append(f"Error: {report.error}")
        return "\n".join(lines)
    if report.usage is not None:
        lines.append(f"Usage: {report.usage:.6g}")
    if report.limit_remaining is not None:
        lines.append(f"Limit remaining: {report.limit_remaining:.6g}")
    if report.balance is not None:
        lines.append(f"Balance: {report.balance:.6g}")
    if report.is_free_tier is not None:
        lines.append(f"Free tier: {'yes' if report.is_free_tier else 'no'}")
    if report.note:
        lines.append(report.note)
    if report.usage is None and report.balance is None and not report.note:
        lines.append("This provider does not publish usage or balance.")
    return "\n".join(lines)
