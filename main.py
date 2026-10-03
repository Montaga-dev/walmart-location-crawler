"""Run the Walmart crawl: each configured ZIP reads its categories, then its products.

    python main.py                    uses config.json
    python main.py --config FILE      uses another config (e.g. a small test run)

Proxy settings come from .env. Results are written to results/<run_id>/.
"""

import argparse
import sys
from pathlib import Path

from common.runner import run_crawl
from common.settings import load_config, load_settings

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Walmart crawl.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        settings = load_settings(ENV_FILE)
        product_ids = config.product_ids()
    except (OSError, ValueError) as error:
        parser.error(str(error))

    targets = {}
    for location in config.locations:
        targets[location.zip] = {
            "product": location.product_requests,
            "category": location.category_requests,
        }

    return run_crawl(
        config=config,
        settings=settings,
        targets=targets,
        product_ids=product_ids,
    )


if __name__ == "__main__":
    sys.exit(main())
