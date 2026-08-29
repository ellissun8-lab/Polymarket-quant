from __future__ import annotations

from pathlib import Path

import scripts.sync_prospective_std0_trades as cli


def test_store_version_is_physically_isolated():
    assert cli.STORE_VERSION == "prospective_v4"


def test_prospective_paths_are_nested_under_legacy_roots(
    settings, tmp_path, monkeypatch
):
    # This test validates path composition only. It never opens the network.
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: settings,
    )

    legacy_raw = Path(settings._project_root) / settings.paths.raw_std0_trades
    legacy_pages = Path(settings._project_root) / settings.paths.raw_api_pages
    legacy_state = Path(settings._project_root) / settings.paths.state

    prospective_raw = legacy_raw / cli.STORE_VERSION / "trades.ndjson"
    prospective_pages = legacy_pages / cli.STORE_VERSION
    prospective_state = legacy_state / cli.STORE_VERSION / "sync_state.db"

    assert prospective_raw.parent.parent == legacy_raw
    assert prospective_pages.parent == legacy_pages
    assert prospective_state.parent.parent == legacy_state

    assert prospective_raw != legacy_raw / "trades.ndjson"
    assert prospective_state != legacy_state / "sync_state.db"
