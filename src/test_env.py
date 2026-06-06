"""Test the dev environment is correctly set up by verifying connectivity to Neo4j
and the GitHub Copilot SDK agent runtime."""

import asyncio
import os

from copilot import CopilotClient, PermissionHandler
from neo4j import GraphDatabase


async def run_pipeline():
    """Run the verification pipeline to test Neo4j connectivity and
    GitHub Copilot SDK agent runtime."""
    print("🚀 Initializing Requirements Engine Verification...")

    # =========================================================================
    # 1. Test Neo4j Connectivity
    # =========================================================================
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USERNAME", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "password")

    try:
        print(f"📡 Connecting to Neo4j at {neo4j_uri}...")
        with GraphDatabase.driver(
            neo4j_uri, auth=(neo4j_user, neo4j_password)
        ) as driver:
            driver.verify_connectivity()
        print("✅ Neo4j Connection Successful!")
    # pylint: disable=broad-except
    except Exception as e:
        print(f"❌ Neo4j Connection Failed: {e}")
        return

    # =========================================================================
    # 2. Test GitHub Copilot SDK Agent Runtime
    # =========================================================================
    print("🤖 Launching GitHub Copilot Agent Engine...")

    # Initialize the background agent runtime (manages the bundled CLI binary)
    async with CopilotClient() as client:
        print("🔗 Establishing an isolated Cloud Session...")

        # Configure your session parameters
        session = await client.create_session(
            model="auto",  # Use a lightweight model for testing
            on_permission_request=PermissionHandler.approve_all,
            streaming=False,
        )
        test_prompt = (
            "You are an expert software architect. Give me a 1-sentence description "
            "of why a Graph database is ideal for tracking software requirement dependencies."
        )
        print(f"💬 Sending prompt to Copilot: '{test_prompt}'")

        # Send message and block until the full response is synthesized
        response = await session.send_and_wait(prompt=test_prompt)

        print("\n🤖 [Copilot Response]:")
        assert response is not None, "No response received from Copilot SDK!"
        print(f"👉 {response.data.content}\n")  # type: ignore
        print("✅ Copilot SDK Execution Loop Successful!")

    # =========================================================================
    # Test Google GenAI Connectivity
    # =========================================================================
    print("🤖 Testing Google GenAI Connectivity...")
    from google import genai

    client = genai.Client()
    test_response = client.models.generate_content(
        model="gemini-2.5-flash", contents="Hi, reply with only the word SUCCESS."
    ).text
    assert test_response is not None, "No response received from Google GenAI!"
    print(f"✅ Google GenAI Connectivity Successful! Response: {test_response.strip()}")


if __name__ == "__main__":
    # Run the async pipeline inside Python 3.14's event loop
    asyncio.run(run_pipeline())
