from __future__ import annotations

from collections.abc import Iterator
from threading import Event

from accessible_ai.models import Account, GenerationSettings, StreamEvent
from accessible_ai.providers.factory import create_provider
from accessible_ai.storage.credentials import CredentialStore


class GenerationService:
    def __init__(self, credentials: CredentialStore):
        self.credentials = credentials

    def generate(
        self,
        account: Account,
        settings: GenerationSettings,
        cancel: Event,
    ) -> Iterator[StreamEvent]:
        provider = create_provider(account, self.credentials)
        yield from provider.generate(settings, cancel)
