"""whitelist: md5sum/sha256sum — read-only, разрешены (в т.ч. в pipe)."""

from agent_coder.tools.run_command import _is_safe_command


def safe(s: str) -> bool:
    return _is_safe_command({"command": s})


def test_checksum_single():
    assert safe("md5sum file.txt")
    assert safe("sha256sum file.txt")
    assert safe("sha1sum a b c")
    assert safe("md5sum -c checksums.txt")
    assert safe("sha256sum --check checksums.sha256")


def test_checksum_pipe():
    assert safe("cat file.txt | md5sum")
    assert safe("md5sum file.txt | head -1")
    assert safe("sha256sum file.txt | sort")


def test_checksum_blocked_mutations():
    assert not safe("md5sum file.txt > out.txt")
    assert not safe("md5sum file.txt; rm x")
    assert not safe("md5sum $(rm -rf x)")
    assert not safe("md5sum file.txt && touch y")
