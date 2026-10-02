import json

import pytest

from common.settings import Proxy, Settings, load_config, load_settings

CATEGORY = "https://www.walmart.com/browse/patio-garden/lawn-mowers/5428_1102182"


def loc(zip_code="10001", products=1, categories=0, **extra) -> dict:
    return {
        "zip": zip_code,
        "product_requests": products,
        "category_requests": categories,
        **extra,
    }


def write_config(tmp_path, **changes) -> object:
    config = {
        "locations": [loc("10001", 2, 1, name="New York"), loc("90001")],
        "categories": [CATEGORY],
        "product_file": "product_ids.json",
        "bootstrap_product": "5301877676",
    }
    config.update(changes)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    (tmp_path / "product_ids.json").write_text('["1", "2", "3"]')
    return path


def test_config_is_loaded(tmp_path):
    config = load_config(write_config(tmp_path))
    assert [loc.zip for loc in config.locations] == ["10001", "90001"]
    assert config.locations[1].name is None
    assert config.max_seconds == 5 * 60 * 60
    assert config.product_ids() == ["1", "2", "3"]


@pytest.mark.parametrize(
    "changes",
    [
        {"locations": []},
        {"locations": [loc(zip_code=10001)]},
        {"locations": [loc(zip_code="1001")]},
        {"locations": [loc(products=-1)]},
        {"locations": [loc(), loc()]},
        {"categories": []},
        {"categories": ["https://example.com/browse/x/1"]},
        {"bootstrap_product": "abc"},
        {"max_seconds": 5 * 60 * 60 + 1},
    ],
)
def test_invalid_config_is_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, **changes))


@pytest.mark.parametrize(
    "ids", [["1", "1", "2"], ["1"], ["1", "2", "x"], ["1", "2", "³"]]
)
def test_product_pool_is_validated(tmp_path, ids):
    config = load_config(write_config(tmp_path))
    config.product_file.write_text(json.dumps(ids))
    with pytest.raises(ValueError):
        config.product_ids()


def test_env_file_and_environment_override(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# local settings\n"
        "USE_PROXY=false\n"
        'PROXIES="http://user:p%40ss@proxy.example:8000,http://u2:pw@proxy2.example:9000"\n'
    )
    settings = load_settings(env, environ={})
    assert not settings.use_proxy
    assert settings.proxies[0] == Proxy("http://proxy.example:8000", "user", "p@ss")
    assert settings.proxies[1].address == "http://proxy2.example:9000"
    assert load_settings(env, environ={"USE_PROXY": "true"}).use_proxy
    assert not load_settings(tmp_path / "missing.env", environ={}).use_proxy


@pytest.mark.parametrize(
    "text",
    [
        "USE_PROXY=yes\n",
        "USE_PROXY=true\nPROXIES=\n",
        "USE_PROXY=true\nPROXIES=https://u:p@h:1\n",
        "USE_PROXY=true\nPROXIES=http://h:1\n",
        "PROXY=http://u:p@h:1\n",
    ],
)
def test_invalid_env_is_rejected(tmp_path, text):
    env = tmp_path / ".env"
    env.write_text(text)
    with pytest.raises(ValueError):
        load_settings(env, environ={})


def test_url_redaction_preserves_unrelated_text():
    settings = Settings(True, (Proxy("http://h:1", "2026", "10"),))
    assert settings.redact("2026-10-03") == "2026-10-03"
    assert settings.redact("http://2026:10@h:1") == "http://[REDACTED]@h:1"


def test_proxy_credentials_are_hidden_in_repr_and_urls():
    settings = Settings(True, (Proxy("http://h:1", "alice", "hunter2"),))
    assert "hunter2" not in repr(settings) and "alice" not in repr(settings)
    text = (
        "failed via http://alice:hunter2@h:1 and http://us%2Fer:p%40ss@other:2 "
        "and http://alice:pa@ssword@proxy.example:8000"
    )
    assert settings.redact(text) == (
        "failed via http://[REDACTED]@h:1 and http://[REDACTED]@other:2 "
        "and http://[REDACTED]@proxy.example:8000"
    )
