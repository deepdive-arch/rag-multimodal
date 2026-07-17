def pytest_configure(config):
    """Keep tests independent from a developer's local .env paths."""
    config.addinivalue_line("markers", "external: requires real Gemini/Pinecone credentials")
