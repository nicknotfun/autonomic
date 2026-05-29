"""Experimental MAS/MRAD summary script.

This script depends on the unsupported `mas` prototype and should not be used
for integration work. The supported direct-amplifier code lives under `amp/`.
"""

from mas.client import Transport, Ampy
import asyncio


async def main() -> None:
    print("Connecting to MA6 and bootstrapping...")
    t = Transport("10.1.0.201", trace=True)

    last_printed_version = -1

    async with Ampy(t) as client:

        def dump_if_changed() -> None:
            nonlocal last_printed_version
            if client.version == last_printed_version:
                return
            last_printed_version = client.version
            # print("Version:", client.version)
            # print("Root:", client.root)
            print("Summary of recognized entities:")
            for _, entity in client.entities.items():
                match entity.type:
                    case "Zones":
                        print(f"{entity.friendly_name}: {entity.get('SourceId')}")
                    case "Sources":
                        print(f"{entity.friendly_name}: {entity.id}")

        await asyncio.sleep(10)
        print("\n\nBootstrap complete, summary detected...")
        dump_if_changed()
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
