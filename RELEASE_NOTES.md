# BlindPilot 0.47.12

This release restores the security scanning behind the scenes.

- CodeQL now analyzes both the Python source and the GitHub Actions workflows, on every push and pull request and on a weekly schedule. The workflow file an earlier change meant to add never actually reached the repository, and this release puts that right. No app behaviour changes.
