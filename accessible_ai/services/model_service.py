from __future__ import annotations

import re

from accessible_ai.models import Account
from accessible_ai.providers.factory import create_provider
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database


_VERSION_PARTS = re.compile(r"\d+")


def _model_version(model_id: str) -> tuple[int, ...]:
    """The version numbers embedded in a provider model identifier.

    Providers rarely give their catalog entries a portable release timestamp,
    but model ids consistently carry their generation, minor revision, and
    often a dated build.  Sorting those numeric parts makes 5.6 precede 5.4
    and 5.1 instead of treating the catalog as an alphabetized list.
    """
    return tuple(int(part) for part in _VERSION_PARTS.findall(model_id))


class ModelService:
    def __init__(self, db: Database, credentials: CredentialStore):
        self.db = db
        self.credentials = credentials

    def cached_models(self, account: Account, order: str = "name_ascending") -> list[str]:
        if account.id is None:
            return []
        models = self.db.get_cached_models(int(account.id), order)
        if order in {"newest", "oldest"}:
            # The database's discovery date stays a stable tie-breaker for
            # identical version numbers.  Python's stable sort preserves it
            # while putting actual model generations and revisions first.
            models.sort(key=_model_version, reverse=order == "newest")
        return models

    def refresh_models(self, account: Account) -> list[str]:
        if account.id is None:
            raise ValueError("Account must be saved before refreshing models")
        provider = create_provider(account, self.credentials)
        models = provider.list_models()
        self.db.replace_model_cache(int(account.id), models)
        return models
