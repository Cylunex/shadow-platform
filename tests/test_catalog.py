from pathlib import Path

import pytest

from scripts.platform_doctor import inspect_platform
from shadow_sdk.catalog import CatalogError, load_app_catalog


def test_published_catalog_and_cross_references_are_valid():
    root = Path(__file__).parents[1]
    catalog = load_app_catalog(root / "catalog" / "apps.yml")
    results = inspect_platform(root)

    assert set(catalog) == {
        "foliant",
        "garden",
        "health",
        "notifications",
        "stock",
        "travel",
    }
    assert catalog["travel"].canonical_url == "https://example.com/travel/"
    assert catalog["stock"].canonical_url == "https://stock.example.com/"
    assert catalog["stock"].auth.mode == "oidc"
    assert catalog["stock"].health_path == "/healthz"
    assert catalog["garden"].auth.mode == "oidc"
    assert catalog["garden"].health_path == "/healthz"
    assert catalog["foliant"].auth.mode == "service-bearer"
    assert catalog["foliant"].auth.groups == ()
    assert catalog["foliant"].media is False
    assert catalog["notifications"].auth.groups == ("shadow-users",)
    assert catalog["notifications"].agent_audience is False
    assert catalog["travel"].llm_models == (
        "chat-default",
        "reasoning-default",
        "vision-default",
    )
    assert not [result for result in results if result.status == "fail"]
    auth_boundary = next(result for result in results if result.check == "catalog auth boundaries")
    oidc_contract = next(result for result in results if result.check == "OIDC client contracts")
    capability_contract = next(
        result for result in results if result.check == "Agent capability contracts"
    )
    assert auth_boundary.status == "pass"
    assert oidc_contract.status == "pass"
    assert capability_contract.status == "pass"


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
