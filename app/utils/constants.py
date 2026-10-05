from app.config import Settings

models_supported = {
    "openai": "openai",
    "ollama": "ollama",
    "gemini": "gemini",
    "deepseek": "deepseek",
    "claude": "claude",
}

# Models billed to the operator per request; they share one monthly allowance.
CLOUD_MODELS = frozenset({"openai", "gemini", "deepseek", "claude"})

# The order the app lists them in.
_MODEL_ORDER = ("ollama", "gemini", "deepseek", "claude", "openai")


def model_names(settings: Settings) -> dict[str, str]:
    """The provider model ID each choice answers with (docs/models.md)."""
    return {
        "openai": settings.openai_model,
        "gemini": settings.gemini_model,
        "deepseek": settings.deepseek_model,
        "claude": settings.claude_model,
    }


def _configured(settings: Settings) -> set[str]:
    """Models this deployment can actually call.

    A PDF question always needs embeddings (OpenAI), so without OPENAI_API_KEY
    nothing is available. A cloud model also needs its own provider key.
    """
    if not settings.openai_api_key:
        return set()
    keys = {
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
        "deepseek": settings.deepseek_api_key,
        "claude": settings.anthropic_api_key,
    }
    return {"ollama"} | {name for name, key in keys.items() if key}


def available_models(settings: Settings, pro: bool = False) -> list[str]:
    """What this account may ask right now, on its plan.

    Free accounts get the local model plus the cloud models named in
    FREE_CLOUD_MODELS (never OpenAI); Pro gets every configured model. A plan
    whose cloud allowance is zero is offered no cloud model at all.
    """
    configured = _configured(settings)
    cloud_allowance = (
        settings.pro_quota_cloud_model_monthly
        if pro
        else settings.quota_cloud_model_monthly
    )
    allowed = []
    for name in _MODEL_ORDER:
        if name not in configured:
            continue
        if name in CLOUD_MODELS:
            if cloud_allowance <= 0:
                continue
            if not pro and name not in settings.free_cloud_models:
                continue
        allowed.append(name)
    return allowed


def pro_only_models(settings: Settings, pro: bool) -> list[str]:
    """Models a free account sees locked: configured, but they need Pro."""
    if pro:
        return []
    offered = set(available_models(settings, pro=False))
    return [m for m in available_models(settings, pro=True) if m not in offered]
