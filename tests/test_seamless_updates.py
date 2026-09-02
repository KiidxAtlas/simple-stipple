"""Regression checks for verified, installer-based desktop updates."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from simple_stipple.platform import updates


def test_current_version_prefers_source_project_version(monkeypatch) -> None:
    monkeypatch.setattr(updates, "_read_version_from_pyproject", lambda: "0.3.5")
    monkeypatch.setattr(updates.metadata, "version", lambda _name: "0.3.4")

    assert updates._detect_current_version() == "0.3.5"


def test_update_staging_path_is_private_and_sanitized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(updates.tempfile, "gettempdir", lambda: str(tmp_path))
    path = updates.update_staging_path("../v 1.2.3", "Windows")
    assert path.parent == tmp_path / "simple-stipple-updates"
    assert path.name == "SimpleStipple-Setup-v-1.2.3.exe"


def test_windows_installer_launches_detached(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "SimpleStipple.exe"
    installer = tmp_path / "SimpleStipple-Setup-1.2.3.exe"
    current.write_bytes(b"current")
    installer.write_bytes(b"installer")
    calls: list[tuple[list[str], dict]] = []

    monkeypatch.setattr(updates.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.sys, "executable", str(current))
    monkeypatch.setattr(
        updates.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )

    assert updates.can_install_update_windows()
    assert updates.launch_windows_installer(installer)
    args, kwargs = calls[0]
    assert args == [
        str(installer),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/CLOSEAPPLICATIONS",
    ]
    assert kwargs["close_fds"] is True


class _OfflineResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]


def _release_payload(digest: str, *, sidecar: bool = True) -> bytes:
    artifact = {
        "name": "SimpleStipple-Setup-99.0.0.exe",
        "browser_download_url": "https://example.invalid/SimpleStipple-Setup-99.0.0.exe",
    }
    if digest:
        artifact["digest"] = digest
    assets = [artifact]
    if sidecar:
        assets.append(
            {
                "name": "SimpleStipple-Setup-99.0.0.exe.sha256",
                "browser_download_url": (
                    "https://example.invalid/SimpleStipple-Setup-99.0.0.exe.sha256"
                ),
            }
        )
    return json.dumps({"tag_name": "v99.0.0", "body": "notes", "assets": assets}).encode()


def test_update_check_uses_verified_sha256_sidecar_offline(monkeypatch) -> None:
    expected = "a" * 64
    responses = iter(
        [
            _OfflineResponse(_release_payload("")),
            _OfflineResponse(f"{expected}  SimpleStipple-Setup-99.0.0.exe\n".encode()),
        ]
    )
    monkeypatch.setattr(updates.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        updates.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses)
    )

    info = updates.check_for_updates()

    assert info is not None
    assert info.sha256 == expected


def test_update_check_rejects_sidecar_for_different_artifact(monkeypatch) -> None:
    responses = iter(
        [
            _OfflineResponse(_release_payload("")),
            _OfflineResponse(f"{'b' * 64}  Other.exe\n".encode()),
        ]
    )
    monkeypatch.setattr(updates.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        updates.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses)
    )

    info = updates.check_for_updates()

    assert info is not None
    assert info.sha256 is None


def test_update_check_rejects_malformed_api_digest_and_uses_sidecar(monkeypatch) -> None:
    expected = "c" * 64
    responses = iter(
        [
            _OfflineResponse(_release_payload("sha256:not-a-digest")),
            _OfflineResponse(f"{expected}  SimpleStipple-Setup-99.0.0.exe\n".encode()),
        ]
    )
    monkeypatch.setattr(updates.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        updates.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses)
    )

    info = updates.check_for_updates()

    assert info is not None
    assert info.sha256 == expected


def _load_build_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "build_standalone.py"
    spec = importlib.util.spec_from_file_location("build_standalone", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_build_manifest_and_platform_commands_are_complete(tmp_path: Path) -> None:
    build = _load_build_script()
    manifest = dict(build.DATA_FILES)
    assert manifest == {
        "src/simple_stipple/ui/style/theme.qss": "simple_stipple/ui/style",
        "src/simple_stipple/ui/style/icons": "simple_stipple/ui/style/icons",
        "src/simple_stipple/resources/tiles": "simple_stipple/resources/tiles",
        "assets/icon.png": "assets",
    }
    for source in manifest:
        assert (build.ROOT / source).exists()

    windows = build.build_command("Windows")
    macos = build.build_command("Darwin")
    assert "--clean" in windows
    assert "--clean" in macos
    assert "--onedir" in windows
    assert "--onefile" not in windows
    assert "--windowed" in windows
    assert "--collect-all=scipy" in windows
    assert str(build.ROOT / "assets" / "icon.ico") in windows
    assert "--osx-bundle-identifier" in macos
    assert str(build.ROOT / "assets" / "icon.png") in macos
    for source in manifest:
        assert any(source in argument for argument in windows)
        assert any(source in argument for argument in macos)

    artifact = tmp_path / "SimpleStipple.exe"
    artifact.write_bytes(b"release artifact")
    sidecar = build.write_sha256(artifact)
    assert sidecar.name == "SimpleStipple.exe.sha256"
    assert sidecar.read_text(encoding="ascii") == (
        f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  SimpleStipple.exe\n"
    )
