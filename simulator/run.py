import argparse
import json
from pathlib import Path

from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a synthetic PayRevive ecosystem")
    parser.add_argument("--config", type=Path, help="Strict JSON GeneratorConfig")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--customers", type=int)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory; never overwritten"
    )
    args = parser.parse_args()
    values = json.loads(args.config.read_text()) if args.config else {}
    values.update(
        {
            key: value
            for key, value in {"seed": args.seed, "customers": args.customers}.items()
            if value is not None
        }
    )
    try:
        summary = export_dataset(GeneratorConfig.model_validate(values), args.output)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
