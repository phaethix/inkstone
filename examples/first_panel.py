import asyncio

from core.api import get_image_provider


async def main() -> None:
    provider = get_image_provider()  # reads AGNES_API_KEY, defaults to Agnes
    out = await provider.generate_single_image("a bespectacled cat, ink-wash style")
    path = "panel.png"  # saved in current working directory
    out.save(path)  # downloaded & persisted on disk
    print(f"saved -> {path}")


asyncio.run(main())
