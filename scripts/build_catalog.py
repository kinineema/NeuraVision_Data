"""Build the report-facing article catalog CSV."""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .literature_utils import load_config, read_jsonl, write_catalog_csv
else:
    from literature_utils import load_config, read_jsonl, write_catalog_csv


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/literature_config.yaml"))
    args = parser.parse_args()
    config = load_config(args.config)
    root = args.config.parent.parent
    articles = read_jsonl(root / config["output"]["articles_jsonl"])
    write_catalog_csv(root / config["output"]["metadata_dir"] / "literature_catalog.csv", articles)


if __name__ == "__main__":
    main()
