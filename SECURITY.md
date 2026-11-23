# Security

**Reporting.** Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Please do not open a public issue for a vulnerability.

**Model.** The studio is a local tool for one user on one PC.
- It listens on 127.0.0.1 only and serves its page only to the window the launcher opened.
- It downloads tools only after you approve the exact version and checksum, from GitHub (or npm for REA).
- It never runs the recovered game. Opening a project in Unity runs its scripts in the editor, so that button asks first.
- AI agents you start run as you and are not sandboxed. Treat recovered files as untrusted.

**Out of scope.** A program already running as your Windows user, a hostile build that fools an agent you have set to auto-approve, and anything in the downloaded third-party tools themselves.

Details: [docs/security.md](docs/security.md).
