"""Panel configuration."""

PROMPT_VERSION = "v2"
HORIZON_DAYS = 30
MIN_PICKS = 2
MAX_PICKS = 5

# Perplexity disabled until paid API tier is enabled.
ENABLED_PANEL_PROVIDERS = ["openai", "gemini", "grok"]

_ALL_PANEL_MODELS = [
    {
        "model_id": "openai-gpt-4o",
        "provider": "openai",
        "api_model": "gpt-4o",
    },
    {
        "model_id": "perplexity-sonar",
        "provider": "perplexity",
        "api_model": "sonar",
    },
    {
        "model_id": "gemini-2.5-flash",
        "provider": "gemini",
        "api_model": "gemini-2.5-flash",
    },
    {
        "model_id": "grok-4.3",
        "provider": "grok",
        "api_model": "grok-4.3",
    },
]

PANEL_MODELS = [m for m in _ALL_PANEL_MODELS if m["provider"] in ENABLED_PANEL_PROVIDERS]

PICKS_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "llm_picks",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "market_bias": {
                    "type": "string",
                    "enum": ["risk_on", "risk_off", "mixed", "unclear"],
                },
                "picks": {
                    "type": "array",
                    "minItems": MIN_PICKS,
                    "maxItems": MAX_PICKS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "direction": {
                                "type": "string",
                                "enum": ["long", "short"],
                            },
                            "conviction": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                            "themes": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "rationale": {"type": "string"},
                            "catalysts": {"type": "string"},
                        },
                        "required": [
                            "ticker",
                            "direction",
                            "conviction",
                            "themes",
                            "rationale",
                            "catalysts",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["market_bias", "picks"],
            "additionalProperties": False,
        },
    },
}
