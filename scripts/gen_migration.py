import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

import fastcache_api.tables as _tables  # noqa: F401 - registers tables on Base.metadata

REPO_ROOT = Path(__file__).parent.parent
VERSIONS_DIR = REPO_ROOT / "src/fastcache_api/alembic/versions"


def main() -> None:
    message = (
        " ".join(sys.argv[1:])
        if len(sys.argv) > 1
        else input("Migration name: ").strip()
    )
    if not message:
        print("Aborted: migration name cannot be empty.")
        sys.exit(1)

    before = {f for f in VERSIONS_DIR.glob("*.py") if f.name != "__init__.py"}

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "gen_migration.sqlite"
        engine = create_engine(f"sqlite:///{db_path}")
        cfg = Config(toml_file=str(REPO_ROOT / "pyproject.toml"))
        cfg.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False)
        )
        command.upgrade(cfg, "head")
        command.revision(cfg, autogenerate=True, message=message)
        engine.dispose()

    new_files = {
        f for f in VERSIONS_DIR.glob("*.py") if f.name != "__init__.py"
    } - before
    for f in sorted(new_files):
        print(f"Generated: {f.name}")


if __name__ == "__main__":
    main()
