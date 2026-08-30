from pathlib import Path

import httpx

from scripts.build_shadow_profile import build_shadow_profile
from scripts.deployment_doctor import inspect_deployment

ROOT = Path(__file__).parents[1]


def _arguments(tmp_path: Path) -> dict[str, object]:
    return {
        "platform_root": ROOT,
        "deployment_path": ROOT / "fixtures" / "conformance-deployment.yml",
        "catalog_path": ROOT / "fixtures" / "conformance-apps.yml",
        "profile_path": ROOT / "fixtures" / "conformance-profile.yml",
        "instances_path": ROOT / "fixtures" / "conformance-instances.yml",
        "plugin_roots": [ROOT / "fixtures" / "conformance-plugin"],
        "output_dir": tmp_path,
    }


def test_live_doctor_verifies_release_and_redacts_runtime_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    target = build_shadow_profile(**_arguments(tmp_path))

    def probe(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer private-value"
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(probe)) as client:
        report = inspect_deployment(
            target,
            environment={
                "SHADOW_CONFORMANCE_BASE_URL": "https://conformance.example.test",
                "SHADOW_CONFORMANCE_TOKEN": "private-value",
            },
            client=client,
        )

    assert report["status"] == "ready"
    assert report["summary"] == {"domains": 1, "ready": 1, "degraded": 0, "failed": 0}
    assert "private-value" not in str(report)
    assert report["domains"][0]["http_status"] == 204
    assert report["conformance_evidence"]["protocol"] == (
        "shadow.conformance-evidence.v1"
    )
    assert report["capability_status"]["summary"]["deployed"] == 7
    assert report["capability_status"]["summary"]["observed"] == 0
    assert {item["maturity"] for item in report["capability_status"]["capabilities"]} == {
        "deployed"
    }


def test_live_doctor_reports_missing_environment_names_without_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    target = build_shadow_profile(**_arguments(tmp_path))

    report = inspect_deployment(target, environment={})

    assert report["status"] == "degraded"
    assert report["domains"][0]["check"] == "configuration"
    assert report["domains"][0]["missing_environment"] == [
        "SHADOW_CONFORMANCE_BASE_URL",
        "SHADOW_CONFORMANCE_TOKEN",
    ]
    assert report["capability_status"]["summary"]["failed"] == 7
