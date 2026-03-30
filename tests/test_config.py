from pathlib import Path

from eu_startups_pipeline.config import load_settings


def test_load_settings_defaults_runtime_llm_to_disabled(tmp_path: Path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pipeline_policy.json").write_text(
        '{"funding_policy": {}, "target_roles": {}, "website_probe_paths": ["/"]}',
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text(
        "\n".join(
            [
                "EU_STARTUPS_SEARCH_URL=https://www.eu-startups.com/directory/page/1/",
                "PIPELINE_POLICY_PATH=config/pipeline_policy.json",
                "ENABLE_RUNTIME_LLM=0",
                "OPENAI_API_KEY=",
                "OPENAI_MODEL=",
                "OPENAI_BASE_URL=https://api.openai.com/v1",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings.enable_runtime_llm is False
    assert settings.openai_api_key == ""
