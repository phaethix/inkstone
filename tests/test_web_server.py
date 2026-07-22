"""Web server configuration and artifact-boundary tests."""

from web import server


def test_compat_only_provider_configuration_is_accepted(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.setenv("PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://images.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "image-key")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_CHAT_API_KEY", "chat-key")

    assert server._providers_configured() is True


def test_output_file_rejects_sibling_directory_with_shared_prefix(tmp_path, monkeypatch):
    output_dir = tmp_path / "comic_out"
    sibling_dir = tmp_path / "comic_out_backup"
    output_dir.mkdir()
    sibling_dir.mkdir()
    outside = sibling_dir / "secret.png"
    outside.write_bytes(b"not an image")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    assert server._is_output_file(str(outside)) is False


def test_path_containment_rejects_shared_prefix_sibling(tmp_path):
    root = tmp_path / "comic_out"
    sibling = tmp_path / "comic_out_backup" / "secret.png"
    root.mkdir()
    sibling.parent.mkdir()
    sibling.write_bytes(b"not an image")

    assert server._is_within(sibling, root) is False
