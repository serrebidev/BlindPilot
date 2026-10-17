# BlindPilot 0.47.9

This release adds two security checks to the project; the app itself is unchanged.

- CodeQL static analysis now runs from a workflow in this repository — on pushes, pull requests, and a weekly schedule — replacing GitHub's default setup, and every pull request gets a dependency review that fails if the change introduces a known-vulnerable dependency. Thanks to Farhan Khan.
