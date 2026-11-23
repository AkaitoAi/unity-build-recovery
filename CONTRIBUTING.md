# Contributing

- Run the tests before opening a pull request: `python -m unittest discover tests`.
- The studio uses only the Python standard library at runtime. Keep it that way.
- The studio never downloads anything without an explicit approval in the page, never turns on an agent's auto-approve mode, and never runs the recovered game. Changes must keep those three rules.
- Keep user-facing wording plain and honest about what was and was not verified.
