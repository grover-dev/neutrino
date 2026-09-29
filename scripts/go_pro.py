# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "open-gopro>=0.22.0",
# ]
# ///

import asyncio

from open_gopro import WiredGoPro


# 1. Define the asynchronous function
async def main():
    async with WiredGoPro() as gopro:
        while not await gopro.is_ready:
            pass
        print("Yay! I'm connected via USB, opened, and ready to send / get data now!")
        while True:
            _ = 1


# 2. Run the asynchronous function via the event loop
if __name__ == "__main__":
    asyncio.run(main())
