from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Source-default renaming is not currently represented by the amp System API."
    )
    parser.parse_args()
    raise SystemExit(
        "This old high-level example is not ported: amp can read source names and "
        "encode SourceNameOp rows, but it does not yet carry the model-specific "
        "default label tables needed to safely restore names."
    )


if __name__ == "__main__":
    main()
