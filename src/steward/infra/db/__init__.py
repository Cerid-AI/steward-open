"""SQLite + sqlite-vec access — connection helper, settings, repos.

Use :func:`steward.infra.db.connect.connect` to open ``inventory.db``;
all connection-level pragmas + extension loads happen there.
"""

from steward.infra.db.connect import connect, vec_version
from steward.infra.db.settings import data_dir, inventory_db_path

__all__ = ["connect", "data_dir", "inventory_db_path", "vec_version"]
