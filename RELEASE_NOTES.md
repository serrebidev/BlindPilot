# BlindPilot 0.27.0

Chat now presents Model as a native list instead of an editable field. The list contains only models in the selected account's catalog, so a typo or a retired model cannot be sent by accident.

Chat, Model order offers Newest first, Oldest first, Name A to Z, and Name Z to A. Newest first is the default, the choice is remembered, and switching the order does not change the model currently selected.

Providers do not consistently expose an actual release date for every model. BlindPilot therefore treats a model as newer when it first appears in that account's refreshed catalog. Models already cached before this release remain older than models newly discovered after it.
