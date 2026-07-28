"""Steward core domain layer — pure logic, no I/O.

Holds the permanode/claim model, policy schema + evaluator, hashing
primitives, tier registry, audit-chain helpers. By import-linter contract,
``steward.core`` must not import ``steward.infra`` or ``steward.cli``.
"""
