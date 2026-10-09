"""W1-W12: Odoo-aware Git repositories. Discovery, state, a small registry and safe operations.

Git runs as the developer, with the developer's SSH agent, keys and credential helpers. Nothing here writes
Git configuration, stores credentials or runs a command that can lose work (reset, clean, force, push).
"""
