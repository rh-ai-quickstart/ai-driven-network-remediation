from mcp_lokistack.config import read_token


class TestReadToken:
    def test_reads_from_file(self, monkeypatch, tmp_path):
        from mcp_lokistack import config

        token_file = tmp_path / "token"
        token_file.write_text("file-token-value\n")
        monkeypatch.setattr(config, "LOKI_TOKEN_PATH", str(token_file))
        assert read_token() == "file-token-value"

    def test_falls_back_to_env_token(self, monkeypatch):
        from mcp_lokistack import config

        monkeypatch.setattr(config, "LOKI_TOKEN_PATH", "")
        monkeypatch.setattr(config, "LOKI_TOKEN", "env-token")
        assert read_token() == "env-token"

    def test_bad_path_falls_back(self, monkeypatch):
        from mcp_lokistack import config

        monkeypatch.setattr(config, "LOKI_TOKEN_PATH", "/nonexistent/path")
        monkeypatch.setattr(config, "LOKI_TOKEN", "fallback")
        assert read_token() == "fallback"
