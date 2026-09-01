from __future__ import annotations

from accessible_ai.models import Account
from accessible_ai.providers.factory import create_provider
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database


class ModelService:
    def __init__(self, db: Database, credentials: CredentialStore):
        self.db = db
        self.credentials = credentials

    def cached_models(self, account: Account) -> list[str]:
        if account.id is None:
            return []
        return self.db.get_cached_models(int(account.id))

    def refresh_models(self, account: Account) -> list[str]:
        if account.id is None:
            raise ValueError("Account must be saved before refreshing models")
        provider = create_provider(account, self.credentials)
        models = provider.list_models()
        self.db.replace_model_cache(int(account.id), models)
        return models
