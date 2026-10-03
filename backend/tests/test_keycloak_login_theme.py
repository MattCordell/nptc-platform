"""The Keycloak login theme, checked without a container (NFR-03, NFR-31).

The theme lives at ``deploy/keycloak/themes/nptc`` and reaches Keycloak by a
bind mount in ``deploy/compose.yml``; the realm file selects it by name. Each
of those three has to agree with the others, and the theme's colours have to
agree with the SPA's. The rendered pages are asserted in
``test_keycloak_pkce_login.py``, against a real Keycloak.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
REALM_FILE = DEPLOY_DIR / "keycloak" / "realm" / "nptc-realm.json"
THEME_DIR = DEPLOY_DIR / "keycloak" / "themes" / "nptc"
LOGIN_DIR = THEME_DIR / "login"
THEME_CSS = LOGIN_DIR / "resources" / "css" / "nptc.css"
APP_CSS = REPO_ROOT / "frontend" / "src" / "styles" / "app.css"

THEME_MOUNT_TARGET = "/opt/keycloak/themes/nptc"

_conftest_spec = importlib.util.spec_from_file_location(
    "_test_keycloak_login_theme_conftest", Path(__file__).parent / "conftest.py"
)
assert _conftest_spec is not None and _conftest_spec.loader is not None
_conftest = importlib.util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
compose_config = _conftest.compose_config

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
#: Tokens the theme repeats from app.css. Every one the theme declares has to
#: match; a token the theme does not declare is not required to exist there.
_SHARED_TOKEN_RE = re.compile(
    r"(?<![\w-])--((?:color|radius|focus-ring|font)-[a-z0-9-]+):\s*([^;]+);"
)


def _tokens(css_path: Path) -> dict[str, str]:
    css = _COMMENT_RE.sub("", css_path.read_text(encoding="utf-8"))
    return {name: " ".join(value.split()) for name, value in _SHARED_TOKEN_RE.findall(css)}


def _theme_mount_source() -> Path:
    """The theme directory compose mounts, read from compose.yml so that a
    moved directory breaks this test rather than diverging silently."""
    volumes: list[str] = compose_config()["services"]["keycloak"]["volumes"]
    mounts = [v for v in volumes if v.split(":")[1] == THEME_MOUNT_TARGET]
    assert len(mounts) == 1, f"expected one mount at {THEME_MOUNT_TARGET}, found {mounts}"
    assert mounts[0].endswith(":ro"), "the theme is mounted read-only"
    return (DEPLOY_DIR / mounts[0].split(":")[0]).resolve()


@pytest.mark.req("NFR-03")
def test_compose_mounts_the_theme_directory_the_repo_commits() -> None:
    assert _theme_mount_source() == THEME_DIR.resolve()
    assert (LOGIN_DIR / "theme.properties").is_file()


@pytest.mark.req("NFR-03")
def test_realm_selects_a_theme_that_compose_mounts() -> None:
    """The failure mode: the realm names a theme that no mount provides.
    Keycloak then falls back to its own look without any error, so this is
    caught here rather than by someone seeing the wrong page."""
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))

    assert realm["loginTheme"] == _theme_mount_source().name


@pytest.mark.req("NFR-31")
def test_theme_colours_match_the_spa_tokens() -> None:
    """The theme repeats the SPA's tokens because Keycloak cannot reach
    ``app.css``. A value changed in one file and not the other is the
    failure this prevents."""
    spa = _tokens(APP_CSS)
    theme = _tokens(THEME_CSS)

    assert theme, "the theme declares no shared tokens"
    for name, value in theme.items():
        assert name in spa, f"--{name} is in the theme but not in app.css"
        assert value == spa[name], f"--{name}: theme has {value!r}, app.css has {spa[name]!r}"


@pytest.mark.req("NFR-31")
def test_every_font_the_theme_loads_ships_with_its_licence() -> None:
    css = _COMMENT_RE.sub("", THEME_CSS.read_text(encoding="utf-8"))
    fonts = re.findall(r'url\("\.\./fonts/([^"]+)"\)', css)

    assert fonts, "the theme loads no fonts"
    for font in fonts:
        assert (LOGIN_DIR / "resources" / "fonts" / font).is_file(), font
    licences = list((LOGIN_DIR / "resources" / "fonts").glob("LICENSE-*.txt"))
    assert len(licences) == 3, "one OFL licence per font family"


@pytest.mark.req("NFR-14")
def test_registration_template_carries_the_notice_and_adds_no_acceptance_checkbox() -> None:
    """ADR-0043: the notice and the links belong on Keycloak's page, and
    acceptance does not. A checkbox here would record nothing."""
    template = (LOGIN_DIR / "register.ftl").read_text(encoding="utf-8")

    assert 'id="kc-registration-notice"' in template
    assert "/privacy" in template
    assert "/terms" in template
    assert 'type="checkbox"' not in template
    assert "termsAccepted" not in template
