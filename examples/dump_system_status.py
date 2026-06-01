from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from _system_example import add_connection_args, discover_or_timeout, selected_hosts

from amp.system import System


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump discovered direct-amplifier system state as JSON."
    )
    add_connection_args(parser)
    args = parser.parse_args()

    hosts = selected_hosts(args)
    with System(hosts, trace=args.trace) as system:
        await discover_or_timeout(system, args)
        snapshot: dict[str, Any] = {"hosts": list(hosts), **system.state.to_json()}

    print(json.dumps(snapshot, indent=2, sort_keys=True))


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
