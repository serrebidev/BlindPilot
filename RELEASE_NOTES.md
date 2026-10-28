# BlindPilot 0.52.3

A Command Code key the server has revoked can be recovered from BlindPilot again.

- **The sign-in check asks the server:** it runs `whoami`, the same round-trip a turn makes, instead of `status`, which only read the credential file on disk. A revoked key no longer shows as "Authentication verified" in the wizard and then fails every turn with "not signed in".
- **Offline is not signed out:** only a definitive refusal from the server counts as signed out. If the server cannot be reached, the credential file is still consulted, as before.
- **Signing in clears the stale key first:** Command Code's `login` used to answer "Already logged in" and do nothing while a credential file existed. The wizard now runs `logout` before `login`, following Command Code's own troubleshooting, so the browser sign-in page opens and the key can be replaced.

Thanks to Farhan Khan for reporting and fixing this.
