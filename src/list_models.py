"""Example script to list available models in your GitHub Copilot subscription."""

import asyncio
import os

from copilot import CopilotClient


async def main():
    """Example script to list available models in your GitHub Copilot subscription."""
    # Pick up your newly verified token environment variable
    token = os.getenv("COPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        print(
            "❌ Error: GITHUB_TOKEN or COPILOT_GITHUB_TOKEN environment variable is missing!"
        )
        return

    # Ensure the background daemon grabs it via the environment block
    os.environ["COPILOT_GITHUB_TOKEN"] = token

    print("🏁 Initializing Copilot Client...")
    # FIX: SubprocessConfig removed. Constructing with keyword argument or empty defaults.
    client = CopilotClient(github_token=token)

    try:
        print("🏁 Connecting to Copilot daemon process...")
        await client.start()

        print("🔍 Querying available models from your subscription seat...")
        models = await client.list_models()

        print(f"\n{'='*60}")
        print(f"Available Copilot Models ({len(models)} total)")
        print(f"{'='*60}")

        for model in sorted(models, key=lambda m: m.id):
            print(f" • ID: {model.id:<30}")

        print(f"{'='*60}\n")
    # pylint: disable=broad-except
    except Exception as e:
        print(f"❌ Failed to fetch models: {e}")
    finally:
        print("🛑 Cleaning up daemon processes...")
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
