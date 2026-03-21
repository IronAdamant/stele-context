"""Tests for the .stele-context.toml configuration system."""

from stele_context.config import load_config, apply_config, _parse_toml_minimal


class TestParseTomlMinimal:
    """Tests for the minimal TOML parser (Python 3.9-3.10 fallback)."""

    def test_empty_string(self):
        assert _parse_toml_minimal("") == {}

    def test_comments_and_blanks(self):
        result = _parse_toml_minimal("# comment\n\n# another")
        assert result == {}

    def test_key_value_string(self):
        result = _parse_toml_minimal('key = "value"')
        assert result == {"key": "value"}

    def test_key_value_int(self):
        result = _parse_toml_minimal("chunk_size = 512")
        assert result == {"chunk_size": 512}

    def test_key_value_float(self):
        result = _parse_toml_minimal("threshold = 0.85")
        assert result == {"threshold": 0.85}

    def test_key_value_bool(self):
        result = _parse_toml_minimal("enabled = true\ndisabled = false")
        assert result == {"enabled": True, "disabled": False}

    def test_key_value_array(self):
        result = _parse_toml_minimal('dirs = [".git", "node_modules"]')
        assert result == {"dirs": [".git", "node_modules"]}

    def test_empty_array(self):
        result = _parse_toml_minimal("items = []")
        assert result == {"items": []}

    def test_section_header(self):
        result = _parse_toml_minimal("[stele-context]\nchunk_size = 512")
        assert result == {"stele-context": {"chunk_size": 512}}

    def test_multiple_sections(self):
        toml = "[stele-context]\nchunk_size = 512\n[other]\nfoo = 1"
        result = _parse_toml_minimal(toml)
        assert result == {"stele-context": {"chunk_size": 512}, "other": {"foo": 1}}

    def test_inline_comment(self):
        result = _parse_toml_minimal("chunk_size = 512 # comment")
        assert result == {"chunk_size": 512}

    def test_full_config(self):
        toml = """
[stele-context]
storage_dir = "/tmp/stele-context"
chunk_size = 512
max_chunk_size = 8192
merge_threshold = 0.75
change_threshold = 0.90
search_alpha = 0.6
skip_dirs = [".git", "node_modules", "dist"]
"""
        result = _parse_toml_minimal(toml)
        assert result["stele-context"]["storage_dir"] == "/tmp/stele-context"
        assert result["stele-context"]["chunk_size"] == 512
        assert result["stele-context"]["max_chunk_size"] == 8192
        assert result["stele-context"]["merge_threshold"] == 0.75
        assert result["stele-context"]["search_alpha"] == 0.6
        assert ".git" in result["stele-context"]["skip_dirs"]


class TestLoadConfig:
    """Tests for loading .stele-context.toml from project root."""

    def test_no_project_root(self):
        assert load_config(None) == {}

    def test_no_config_file(self, tmp_path):
        assert load_config(tmp_path) == {}

    def test_load_valid_config(self, tmp_path):
        config_file = tmp_path / ".stele-context.toml"
        config_file.write_text("[stele-context]\nchunk_size = 512\n")
        result = load_config(tmp_path)
        assert result == {"chunk_size": 512}

    def test_load_config_without_section(self, tmp_path):
        config_file = tmp_path / ".stele-context.toml"
        config_file.write_text("chunk_size = 512\n")
        result = load_config(tmp_path)
        assert result == {"chunk_size": 512}

    def test_load_malformed_config(self, tmp_path):
        config_file = tmp_path / ".stele-context.toml"
        config_file.write_text("this is not valid toml {{{")
        result = load_config(tmp_path)
        # Should not raise, returns something (may be partial parse or empty)
        assert isinstance(result, dict)


class TestApplyConfig:
    """Tests for merging config file values with constructor params."""

    def test_explicit_params_win(self):
        config = {"chunk_size": 512, "search_alpha": 0.3}
        result = apply_config(config, chunk_size=1024)
        assert result["chunk_size"] == 1024
        assert result["search_alpha"] == 0.3

    def test_config_values_used_when_no_explicit(self):
        config = {"chunk_size": 512, "max_chunk_size": 8192}
        result = apply_config(config)
        assert result["chunk_size"] == 512
        assert result["max_chunk_size"] == 8192

    def test_empty_config(self):
        result = apply_config({})
        assert result == {}

    def test_skip_dirs_from_config(self):
        config = {"skip_dirs": [".git", "vendor"]}
        result = apply_config(config)
        assert result["skip_dirs"] == {".git", "vendor"}

    def test_skip_dirs_explicit_wins(self):
        config = {"skip_dirs": [".git", "vendor"]}
        result = apply_config(config, skip_dirs={"custom"})
        assert result["skip_dirs"] == {"custom"}

    def test_invalid_type_in_config(self):
        config = {"chunk_size": "not_a_number"}
        result = apply_config(config)
        assert "chunk_size" not in result


class TestEngineConfigIntegration:
    """Tests that Stele engine loads .stele-context.toml correctly."""

    def test_engine_reads_config(self, tmp_path):
        config_file = tmp_path / ".stele-context.toml"
        config_file.write_text(
            "[stele-context]\nchunk_size = 512\nsearch_alpha = 0.3\n"
        )
        # Create .git so project root detection works
        (tmp_path / ".git").mkdir()

        from stele_context.engine import Stele

        engine = Stele(
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        assert engine.chunk_size == 512
        assert engine.search_alpha == 0.3
        # Defaults still applied for unset values
        assert engine.max_chunk_size == 4096

    def test_explicit_params_override_config(self, tmp_path):
        config_file = tmp_path / ".stele-context.toml"
        config_file.write_text("[stele-context]\nchunk_size = 512\n")
        (tmp_path / ".git").mkdir()

        from stele_context.engine import Stele

        engine = Stele(
            project_root=str(tmp_path),
            chunk_size=1024,
            enable_coordination=False,
        )
        assert engine.chunk_size == 1024

    def test_no_config_uses_defaults(self, tmp_path):
        (tmp_path / ".git").mkdir()

        from stele_context.engine import Stele

        engine = Stele(
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        assert engine.chunk_size == 256
        assert engine.search_alpha == 0.7
