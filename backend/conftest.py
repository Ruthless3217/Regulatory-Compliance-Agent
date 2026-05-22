"""Pytest configuration for the backend test suite."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "pinecone: live integration test against a real Pinecone index "
        "(skipped unless PINECONE_API_KEY and PINECONE_INDEX_NAME are set)",
    )
