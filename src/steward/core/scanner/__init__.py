"""Pure-domain scanner types (protocols, value objects).

Concrete walker / watcher implementations live under
``steward.infra.scanner``. By import-linter contract, ``steward.core``
cannot import ``steward.infra``, so the abstractions live here and the
adapters live there.
"""
