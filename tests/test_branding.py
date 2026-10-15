"""Public-facing name and disclaimer, as the Minecraft Usage Guidelines ask for."""

from pathlib import Path

from mcclaimer import APP_NAME, APP_TITLE, DISCLAIMER

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def first_nonblank_lines(text: str, count: int) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()][:count]


def test_title_keeps_minecraft_out_of_the_primary_name():
    assert APP_TITLE == "MCNameClaimer — Username Utility for Minecraft: Java Edition"
    assert "Minecraft" not in APP_NAME


def test_disclaimer_wording():
    assert DISCLAIMER == (
        "NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT."
    )


def test_readme_title_is_followed_by_the_disclaimer():
    title, disclaimer = first_nonblank_lines(read("README.md"), 2)
    assert title == f"# {APP_TITLE}"
    assert disclaimer == f"**{DISCLAIMER}**"


def test_readme_discloses_automatic_claiming_and_approval_status():
    readme = read("README.md")
    assert "automatically send one name-change request" in readme
    assert "not currently approved" in readme
    assert "not guaranteed" in readme
    assert "community documentation, not an official Mojang source" in readme


def test_supporting_documents_carry_the_disclaimer():
    for name in ("PRIVACY.md", "SECURITY.md"):
        assert DISCLAIMER in read(name)


def test_privacy_statement_explains_how_to_revoke_access():
    assert "https://account.live.com/consent/Manage" in read("PRIVACY.md")
