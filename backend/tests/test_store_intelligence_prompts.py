from __future__ import annotations

from app.services.bigcommerce_chat.prompts import (
    build_analytics_cache_prompt,
    build_dimension_aliases_section,
    build_system_prompt,
)


def test_prompts_include_dimension_aliases():
    aliases = build_dimension_aliases_section()
    assert "Bush School" in aliases or "bush school" in aliases.lower()

    system_prompt = build_system_prompt()
    cache_prompt = build_analytics_cache_prompt()

    assert "Store Intelligence" in system_prompt
    assert "Store Intelligence" in cache_prompt
    assert "bc_orders" in cache_prompt
    assert "total_inc_tax" in system_prompt
    assert "total_inc_tax" in cache_prompt