"""Ariadne's Thread registers nothing in the agent.

The plugin is a dashboard API router (``dashboard/plugin_api.py``) plus a Desktop
page (``desktop/plugin.js``). ``hermes plugins doctor`` loads native packages
through ``register(ctx)``, so this entrypoint exists and declares no tools or hooks.
"""


def register(ctx):
    return None
