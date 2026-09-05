from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading

import wx

from accessible_ai.models import (
    API_MODE_AUTO,
    API_MODE_CHAT,
    API_MODE_LABELS,
    API_MODE_MESSAGES,
    API_MODE_RESPONSES,
    Account,
    PROVIDER_CLAUDE,
    PROVIDER_DEEPSEEK,
    PROVIDER_GEMINI,
    PROVIDER_KIMI,
    PROVIDER_LABELS,
    PROVIDER_MOONSHOT,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENROUTER,
    PROVIDER_Z_AI,
)
from accessible_ai.providers.config import apply_builtin_provider_defaults, is_builtin_provider
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore, CredentialStoreError
from accessible_ai.storage.database import Database


PROVIDER_ORDER = [
    PROVIDER_OPENROUTER,
    PROVIDER_OPENAI,
    PROVIDER_CLAUDE,
    PROVIDER_GEMINI,
    PROVIDER_Z_AI,
    PROVIDER_MOONSHOT,
    PROVIDER_KIMI,
    PROVIDER_DEEPSEEK,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENAI_COMPATIBLE,
]

# Said in the account dialog when a provider is chosen, so the requirements of
# each built-in service are stated in words rather than implied by which fields
# happen to be visible.
BUILTIN_PROVIDER_NOTES = {
    PROVIDER_OPENROUTER: (
        "OpenRouter connection addresses are built in. Enter an account name and your OpenRouter API key. "
        "You do not need to enter or verify any URL."
    ),
    PROVIDER_OPENAI: (
        "OpenAI connection addresses are built in. Enter an account name and your OpenAI API key. "
        "You do not need to enter or verify any URL."
    ),
    PROVIDER_CLAUDE: (
        "Claude connection addresses are built in. Enter an account name and your Anthropic API key. "
        "Claude uses Anthropic's Messages protocol. File attachments are available on OpenRouter accounts only."
    ),
    PROVIDER_GEMINI: (
        "Gemini connection addresses are built in. Enter an account name and your Google AI Studio API key. "
        "You do not need to enter or verify any URL."
    ),
    PROVIDER_Z_AI: (
        "Z.AI connection addresses are built in. Enter an account name and your Z.AI API key. "
        "You do not need to enter or verify any URL."
    ),
    PROVIDER_MOONSHOT: (
        "Moonshot AI connection addresses are built in, using the international service at api.moonshot.ai. "
        "Enter an account name and your Moonshot AI API key."
    ),
    PROVIDER_KIMI: (
        "Kimi connection addresses are built in, using Moonshot's China service at api.moonshot.cn. "
        "Enter an account name and your Kimi API key. A Moonshot AI key from the international service will "
        "not work here, and a Kimi key will not work on a Moonshot AI account."
    ),
    PROVIDER_DEEPSEEK: (
        "DeepSeek connection addresses are built in. Enter an account name and your DeepSeek API key. "
        "You do not need to enter or verify any URL."
    ),
    PROVIDER_OPENCODE_GO: (
        "OpenCode Go connection addresses and per-model protocol routing are built in. Enter an account "
        "name and your OpenCode Go API key. You do not need to enter or verify any URL."
    ),
}

# Where CredentialStore keeps the key on this platform, for the two places the
# dialog names it. Off Windows the store is whatever keyring backend the desktop
# provides: the macOS Keychain, or the Secret Service on Linux.
CREDENTIAL_STORE_NAME = "Windows Credential Manager" if os.name == "nt" else "the system keychain"

CUSTOM_PROVIDER_NOTE = (
    "This account type is only for a server that implements an OpenAI-compatible API and is not one "
    "of the built-in services. Its server address is the only connection URL you need to supply."
)
API_MODE_ORDER = [API_MODE_AUTO, API_MODE_CHAT, API_MODE_RESPONSES, API_MODE_MESSAGES]

STANDARD_MODELS_ENDPOINT = "/models"
STANDARD_CHAT_ENDPOINT = "/chat/completions"
STANDARD_RESPONSES_ENDPOINT = "/responses"
STANDARD_MESSAGES_ENDPOINT = "/messages"


logger = logging.getLogger(__name__)


class AccountEditorDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        db: Database,
        credentials: CredentialStore,
        account: Account | None = None,
    ):
        super().__init__(
            parent,
            title="Account Settings",
            size=(680, 650),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.db = db
        self.credentials = credentials
        self.account = account or Account()
        self.is_new = account is None

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        main_grid = wx.FlexGridSizer(cols=2, vgap=9, hgap=12)
        main_grid.AddGrowableCol(1, 1)

        self.name = self._add_text(main_grid, panel, "Account name:", "Account name")

        main_grid.Add(wx.StaticText(panel, label="Provider:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.provider = wx.Choice(panel, choices=[PROVIDER_LABELS[p] for p in PROVIDER_ORDER])
        self.provider.SetName("Provider")
        main_grid.Add(self.provider, 1, wx.EXPAND)

        self.api_key_label = wx.StaticText(panel, label="API key:")
        main_grid.Add(self.api_key_label, 0, wx.ALIGN_CENTER_VERTICAL)
        self.api_key = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        self.api_key.SetName("API key")
        main_grid.Add(self.api_key, 1, wx.EXPAND)
        self.default_model = self._add_text(
            main_grid, panel, "Default model, optional:", "Default model"
        )
        self.timeout = self._add_text(
            main_grid,
            panel,
            "Request timeout in seconds:",
            "Request timeout in seconds",
        )

        main_grid.Add(wx.StaticText(panel, label="Streaming:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.streaming = wx.CheckBox(panel, label="Stream responses")
        self.streaming.SetName("Stream responses")
        main_grid.Add(self.streaming, 0, wx.ALIGN_LEFT)

        outer.Add(main_grid, 0, wx.EXPAND | wx.ALL, 12)

        self.provider_note = wx.StaticText(panel, label="")
        self.provider_note.SetName("Provider connection information")
        self.provider_note.Wrap(640)
        outer.Add(self.provider_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        # A wxStaticBoxSizer's contents must be children of its wxStaticBox, not
        # of the surrounding panel, or wxWidgets warns and the box does not
        # reliably hide its contents with itself.
        self.compat_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Custom OpenAI-compatible server")
        compat_parent = self.compat_box.GetStaticBox()
        compat_grid = wx.FlexGridSizer(cols=2, vgap=9, hgap=12)
        compat_grid.AddGrowableCol(1, 1)

        self.base_url = self._add_text(
            compat_grid,
            compat_parent,
            "Server base URL:",
            "OpenAI-compatible server base URL",
        )

        compat_grid.Add(
            wx.StaticText(compat_parent, label="API mode:"), 0, wx.ALIGN_CENTER_VERTICAL
        )
        self.api_mode = wx.Choice(
            compat_parent, choices=[API_MODE_LABELS[m] for m in API_MODE_ORDER]
        )
        self.api_mode.SetName("API mode")
        compat_grid.Add(self.api_mode, 1, wx.EXPAND)
        self.compat_box.Add(compat_grid, 0, wx.EXPAND | wx.ALL, 10)

        compat_note = wx.StaticText(
            compat_parent,
            label=(
                "Only a custom OpenAI-compatible server needs a URL. Standard endpoint paths are "
                "configured automatically as /models, /chat/completions, /responses, and /messages."
            ),
        )
        compat_note.Wrap(620)
        self.compat_box.Add(compat_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        outer.Add(self.compat_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        advanced_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Advanced request settings")
        advanced_parent = advanced_box.GetStaticBox()
        advanced_grid = wx.FlexGridSizer(cols=2, vgap=9, hgap=12)
        advanced_grid.AddGrowableCol(1, 1)

        advanced_grid.Add(
            wx.StaticText(advanced_parent, label="Custom HTTP headers, JSON object:"),
            0,
            wx.ALIGN_TOP,
        )
        self.custom_headers = wx.TextCtrl(advanced_parent, style=wx.TE_MULTILINE, size=(-1, 70))
        self.custom_headers.SetName("Custom HTTP headers JSON")
        advanced_grid.Add(self.custom_headers, 1, wx.EXPAND)

        advanced_grid.Add(
            wx.StaticText(advanced_parent, label="Custom request body fields, JSON object:"),
            0,
            wx.ALIGN_TOP,
        )
        self.custom_body = wx.TextCtrl(advanced_parent, style=wx.TE_MULTILINE, size=(-1, 70))
        self.custom_body.SetName("Custom request body fields JSON")
        advanced_grid.Add(self.custom_body, 1, wx.EXPAND)

        advanced_box.Add(advanced_grid, 1, wx.EXPAND | wx.ALL, 10)
        outer.Add(advanced_box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        note = wx.StaticText(
            panel,
            label=(
                f"API keys are stored in {CREDENTIAL_STORE_NAME}. OpenRouter response caching is "
                "always disabled and cached OpenRouter responses are rejected."
            ),
        )
        note.Wrap(640)
        outer.Add(note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        buttons = wx.StdDialogButtonSizer()
        ok_button = wx.Button(panel, wx.ID_OK)
        cancel_button = wx.Button(panel, wx.ID_CANCEL)
        buttons.AddButton(ok_button)
        buttons.AddButton(cancel_button)
        buttons.Realize()
        ok_button.SetDefault()
        outer.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        panel.SetSizer(outer)

        self.provider.Bind(wx.EVT_CHOICE, self.on_provider_changed)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self._load()

    @staticmethod
    def _add_text(
        grid: wx.FlexGridSizer,
        parent: wx.Window,
        label: str,
        name: str,
        password: bool = False,
    ) -> wx.TextCtrl:
        grid.Add(wx.StaticText(parent, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
        style = wx.TE_PASSWORD if password else 0
        control = wx.TextCtrl(parent, style=style)
        control.SetName(name)
        grid.Add(control, 1, wx.EXPAND)
        return control

    def _selected_provider(self) -> str:
        selection = self.provider.GetSelection()
        if selection == wx.NOT_FOUND:
            return PROVIDER_OPENROUTER
        return PROVIDER_ORDER[selection]

    def _load(self) -> None:
        if is_builtin_provider(self.account.provider):
            apply_builtin_provider_defaults(self.account)

        self.name.SetValue(self.account.name)
        provider_index = (
            PROVIDER_ORDER.index(self.account.provider)
            if self.account.provider in PROVIDER_ORDER
            else 0
        )
        self.provider.SetSelection(provider_index)

        if self.account.id is not None:
            # An empty field on OK leaves the stored key as it is (see on_ok),
            # so the label has to say so; the field itself gives nothing away.
            self.api_key_label.SetLabel("API key, blank keeps the stored key:")
            try:
                self.api_key.SetValue(self.credentials.get_api_key(int(self.account.id)))
            except CredentialStoreError:
                self.api_key.SetValue("")

        self.default_model.SetValue(self.account.default_model)
        self.timeout.SetValue(str(self.account.timeout_seconds))
        self.streaming.SetValue(self.account.streaming)
        self.base_url.SetValue(
            self.account.base_url if self.account.provider == PROVIDER_OPENAI_COMPATIBLE else ""
        )
        api_index = (
            API_MODE_ORDER.index(self.account.api_mode)
            if self.account.api_mode in API_MODE_ORDER
            else 0
        )
        self.api_mode.SetSelection(api_index)
        self.custom_headers.SetValue(
            json.dumps(self.account.custom_headers, ensure_ascii=False, indent=2)
        )
        self.custom_body.SetValue(
            json.dumps(self.account.custom_body, ensure_ascii=False, indent=2)
        )
        self._update_provider_ui()

    def on_provider_changed(self, event: wx.CommandEvent) -> None:
        self._update_provider_ui()
        event.Skip()

    def _update_provider_ui(self) -> None:
        provider = self._selected_provider()
        is_compatible = provider == PROVIDER_OPENAI_COMPATIBLE
        self.compat_box.ShowItems(is_compatible)
        self.compat_box.GetStaticBox().Show(is_compatible)

        self.provider_note.SetLabel(BUILTIN_PROVIDER_NOTES.get(provider, CUSTOM_PROVIDER_NOTE))
        self.provider_note.Wrap(640)
        self.Layout()

    def on_ok(self, event: wx.CommandEvent) -> None:
        name = self.name.GetValue().strip()
        if not name:
            wx.MessageBox(
                "Account name is required.", "Account Settings", wx.OK | wx.ICON_ERROR, self
            )
            self.name.SetFocus()
            return

        provider = self._selected_provider()
        api_key = self.api_key.GetValue().strip()
        if is_builtin_provider(provider) and not api_key:
            wx.MessageBox(
                f"An API key is required for {PROVIDER_LABELS[provider]}.",
                "Account Settings",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self.api_key.SetFocus()
            return

        try:
            timeout = float(self.timeout.GetValue().strip())
            if timeout <= 0:
                raise ValueError
        except ValueError:
            wx.MessageBox(
                "Timeout must be a number greater than zero.",
                "Account Settings",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self.timeout.SetFocus()
            return

        try:
            headers = json.loads(self.custom_headers.GetValue().strip() or "{}")
            body = json.loads(self.custom_body.GetValue().strip() or "{}")
            if not isinstance(headers, dict) or not isinstance(body, dict):
                raise ValueError("Custom headers and request body must both be JSON objects.")
            headers = {str(k): str(v) for k, v in headers.items()}
        except (json.JSONDecodeError, ValueError) as exc:
            wx.MessageBox(
                f"Invalid custom JSON: {exc}", "Account Settings", wx.OK | wx.ICON_ERROR, self
            )
            return

        self.account.name = name
        self.account.provider = provider
        self.account.default_model = self.default_model.GetValue().strip()
        self.account.timeout_seconds = timeout
        self.account.streaming = self.streaming.GetValue()
        self.account.custom_headers = headers
        self.account.custom_body = body

        if is_builtin_provider(provider):
            apply_builtin_provider_defaults(self.account)
        else:
            base_url = self.base_url.GetValue().strip()
            if not base_url:
                wx.MessageBox(
                    "A server base URL is required for a custom OpenAI-compatible account.",
                    "Account Settings",
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
                self.base_url.SetFocus()
                return
            self.account.base_url = base_url
            self.account.models_endpoint = STANDARD_MODELS_ENDPOINT
            self.account.chat_endpoint = STANDARD_CHAT_ENDPOINT
            self.account.responses_endpoint = STANDARD_RESPONSES_ENDPOINT
            self.account.messages_endpoint = STANDARD_MESSAGES_ENDPOINT
            selection = self.api_mode.GetSelection()
            self.account.api_mode = API_MODE_ORDER[selection if selection != wx.NOT_FOUND else 0]

        was_new = self.account.id is None
        account_id: int | None = None
        try:
            account_id = self.db.save_account(self.account)
            # Blank means keep whatever key is stored, as the field's label says.
            if api_key:
                self.credentials.set_api_key(account_id, api_key)
        except sqlite3.IntegrityError:
            logger.warning("Could not save account because its name is already in use: %s", name)
            wx.MessageBox(
                "An account with that name already exists.",
                "Account Settings",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self.name.SetFocus()
            return
        except CredentialStoreError as exc:
            logger.exception("Could not store credentials for account %s", name)
            if was_new and account_id is not None:
                try:
                    self.db.delete_account(account_id)
                    self.account.id = None
                except Exception:
                    logger.exception(
                        "Could not roll back new account metadata after credential storage failed"
                    )
            wx.MessageBox(
                f"Could not store the API key in {CREDENTIAL_STORE_NAME}: {exc}",
                "Account Settings",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        logger.info("Saved account name=%s provider=%s", self.account.name, self.account.provider)
        self.EndModal(wx.ID_OK)


class AccountsDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        db: Database,
        credentials: CredentialStore,
        model_service: ModelService,
    ):
        super().__init__(
            parent,
            title="Accounts",
            size=(650, 480),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.db = db
        self.credentials = credentials
        self.model_service = model_service
        self.accounts: list[Account] = []
        self.model_action_running = False

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(panel, label="Accounts:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.listbox = wx.ListBox(panel)
        self.listbox.SetName("Accounts")
        outer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 12)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.add_button = wx.Button(panel, label="&Add")
        self.edit_button = wx.Button(panel, label="&Edit")
        self.delete_button = wx.Button(panel, label="&Delete")
        self.test_button = wx.Button(panel, label="&Test account")
        self.refresh_button = wx.Button(panel, label="Refresh &models")
        for button in [
            self.add_button,
            self.edit_button,
            self.delete_button,
            self.test_button,
            self.refresh_button,
        ]:
            row.Add(button, 0, wx.RIGHT, 8)
        outer.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.close_button = wx.Button(panel, wx.ID_CLOSE, "Close")
        outer.Add(self.close_button, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        panel.SetSizer(outer)

        self.add_button.Bind(wx.EVT_BUTTON, self.on_add)
        self.edit_button.Bind(wx.EVT_BUTTON, self.on_edit)
        self.delete_button.Bind(wx.EVT_BUTTON, self.on_delete)
        self.test_button.Bind(wx.EVT_BUTTON, self.on_test)
        self.refresh_button.Bind(wx.EVT_BUTTON, self.on_refresh)
        self.close_button.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_CLOSE))
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self.on_edit)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.reload()

    def reload(self, select_account_id: int | None = None) -> None:
        self.accounts = self.db.list_accounts()
        self.listbox.Set(
            [f"{a.name}, {PROVIDER_LABELS.get(a.provider, a.provider)}" for a in self.accounts]
        )
        if not self.accounts:
            return
        selection = 0
        if select_account_id is not None:
            for index, account in enumerate(self.accounts):
                if account.id == select_account_id:
                    selection = index
                    break
        self.listbox.SetSelection(selection)

    def selected(self) -> Account | None:
        index = self.listbox.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self.accounts):
            return None
        return self.accounts[index]

    def on_add(self, event: wx.CommandEvent) -> None:
        dialog = AccountEditorDialog(self, self.db, self.credentials)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.reload(dialog.account.id)
        finally:
            dialog.Destroy()

    def on_edit(self, event: wx.CommandEvent) -> None:
        account = self.selected()
        if not account:
            return
        dialog = AccountEditorDialog(self, self.db, self.credentials, account)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.reload(dialog.account.id)
        finally:
            dialog.Destroy()

    def on_delete(self, event: wx.CommandEvent) -> None:
        account = self.selected()
        if not account or account.id is None:
            return
        answer = wx.MessageBox(
            f"Delete account '{account.name}'?",
            "Delete Account",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self,
        )
        if answer != wx.YES:
            return
        try:
            self.credentials.delete_api_key(int(account.id))
        except CredentialStoreError:
            logger.exception(
                "Could not remove credentials for deleted account name=%s", account.name
            )
        self.db.delete_account(int(account.id))
        logger.info("Deleted account name=%s provider=%s", account.name, account.provider)
        self.reload()

    def _run_model_action(self, success_prefix: str) -> None:
        if self.model_action_running:
            return
        account = self.selected()
        if not account:
            return
        self.model_action_running = True
        for control in (
            self.add_button,
            self.edit_button,
            self.delete_button,
            self.test_button,
            self.refresh_button,
            self.close_button,
        ):
            control.Disable()

        def work() -> None:
            try:
                models = self.model_service.refresh_models(account)
                wx.CallAfter(self._model_action_done, f"{success_prefix} {len(models)} models.")
            except Exception as exc:
                logger.exception("Model action failed for account name=%s", account.name)
                wx.CallAfter(self._model_action_done, f"Error: {exc}", True)

        threading.Thread(target=work, daemon=True).start()

    def _model_action_done(self, message: str, is_error: bool = False) -> None:
        self.model_action_running = False
        for control in (
            self.add_button,
            self.edit_button,
            self.delete_button,
            self.test_button,
            self.refresh_button,
            self.close_button,
        ):
            control.Enable()
        wx.MessageBox(
            message, "Accounts", wx.OK | (wx.ICON_ERROR if is_error else wx.ICON_INFORMATION), self
        )

    def on_close(self, event: wx.CloseEvent) -> None:
        if self.model_action_running:
            wx.MessageBox(
                "Wait for the account test or model refresh to finish before closing this window.",
                "Accounts",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            event.Veto()
            return
        event.Skip()

    def on_test(self, event: wx.CommandEvent) -> None:
        self._run_model_action("Connection succeeded. The provider returned")

    def on_refresh(self, event: wx.CommandEvent) -> None:
        self._run_model_action("Model list refreshed with")
