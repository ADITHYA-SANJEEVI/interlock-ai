"""Development refresh of only the read functions, restricted to test databases."""

from pathlib import Path

from sqlalchemy import create_engine, text

from tools.pass4.fresh import BASE


def main():
    for domain in ("control", "seller"):
        url = BASE + "test_" + domain
        assert url.endswith("/test_" + domain)
        with create_engine(url, hide_parameters=True).begin() as db:
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0020_pass6_boundaries"
            source = Path("migrations/sql/0020_" + domain + "_boundaries.sql").read_text()
            db.exec_driver_sql(source.replace("%", "%%"))


if __name__ == "__main__":
    main()
