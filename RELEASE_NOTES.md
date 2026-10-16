# BlindPilot 0.47.7

This release hardens the build pipeline; the app itself is unchanged.

- The CI and release workflows now pin every GitHub Action to a full commit SHA instead of a mutable tag, so a moved or compromised tag cannot change what runs, and Dependabot is configured to keep the pins current. Thanks to Farhan Khan for the hardening.
