from pathlib import Path

import pytest

from scripts.platform_doctor import inspect_platform
from shadow_sdk.catalog import CatalogError, load_app_catalog


def test_published_catalog_and_cross_references_are_valid():
    root = Path(__file__).parents[1]
    catalog = load_app_catalog(root / "catalog" / "apps.yml")
    results = inspect_platform(root)

    assert set(catalog) == {"foliant", "garden", "health", "stock", "travel"}
    assert catalog["travel"].canonical_url == "https://cylunex.top/travel/"
    assert catalog["travel"].llm_models == (
        "chat-default",
        "reasoning-default",
        "vision-default",
    )
    assert not [result for result in results if result.status == "fail"]


def test_strict_doctor_checks_deploy_time_files_without_crashing():
    root = Path(__file__).parents[1]

    results = inspect_platform(root, strict=True)

    failures = {result.check for result in results if result.status == "fail"}
    assert "LLM registry schema" in failures
    assert "Agent registry schema" in failures
    assert "OIDC clients" in failures
    assert "production placeholders" in failures


def test_catalog_rejects_duplicate_urls(tmp_path):
    catalog = tmp_path / "apps.yml"
    catalog.write_text(
        """
version: 1
apps:
  first:
    title: First
    owner: owner
    lifecycle: production
    kind: web
    canonical_url: https://example.com/
    auth: {mode: public, groups: []}
    health_path: /healthz
    media: false
    llm_models: []
    agent_audience: false
  second:
    title: Second
    owner: owner
    lifecycle: production
    kind: web
    canonical_url: https://example.com
    auth: {mode: public, groups: []}
    health_path: /healthz
    media: false
    llm_models: []
    agent_audience: false
""",
        encoding="utf-8",
    )

    with pytest.raises(CatalogError, match="duplicate catalog URL"):
        load_app_catalog(catalog)
