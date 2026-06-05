"""Runtime configuration for ProtoProject."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """Process configuration loaded from environment variables."""

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "password"
    embedding_dimension: int = 32


def load_config() -> AppConfig:
    """Load configuration from the environment."""

    return AppConfig(
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_username=os.getenv("NEO4J_USERNAME", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "password"),
        embedding_dimension=int(os.getenv("PROTOPROJECT_EMBEDDING_DIM", "32")),
    )
