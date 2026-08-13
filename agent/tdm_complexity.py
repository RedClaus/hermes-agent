"""TDM complexity header emitter (#175).

Classifies the current-turn task complexity and emits an X-TDM-Complexity
header on outbound TDM requests. The value is derived from task intent
(platform/session type), NEVER from payload size.

Config (config.yaml):
  tdm:
    complexity:
      enabled: true              # default: true when provider is TDM
      default: moderate          # default for interactive sessions
      cron: low                  # default for cron/scheduled sessions
      # Optional explicit override (highest priority):
      # override: high
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

TDM_COMPLEXITY_HEADER = "X-TDM-Complexity"
VALID_VALUES = ("low", "moderate", "high")

# Default mapping: task context → complexity
DEFAULT_COMPLEXITY_MAP = {
    "cron": "low",
    "interactive": "moderate",
}


def _is_tdm_request(provider: str, base_url: str) -> bool:
    """Check if this request is going to TDM.

    Detection (any match):
    - provider name contains 'tdm' (case-insensitive)
    - base_url contains '192.168.1.250:9100'
    - base_url contains ':9100' (TDM's standard port)
    """
    provider_lower = (provider or "").lower()
    base_url_lower = (base_url or "").lower()
    return (
        "tdm" in provider_lower
        or "192.168.1.250:9100" in base_url_lower
        or ":9100" in base_url_lower
    )


def _is_cron_context(platform: str) -> bool:
    """Detect cron/scheduled job context."""
    if platform == "cron":
        return True
    if os.environ.get("HERMES_CRON_SESSION") == "1":
        return True
    return False


def classify_complexity(
    *,
    provider: str,
    base_url: str,
    platform: str,
    config: Optional[dict] = None,
    **_extra: Any,
) -> Optional[str]:
    """Classify the current-turn complexity for TDM routing.

    Returns one of 'low', 'moderate', 'high', or None (don't emit header).

    Priority (highest first):
    1. Config override (tdm.complexity.override) — explicit pin
    2. Env var HERMES_TDM_COMPLEXITY — runtime override
    3. Config mapping (tdm.complexity.cron / .default)
    4. Built-in defaults: cron → low, interactive → moderate

    NEVER uses payload size, token count, or message content.
    """
    # Only emit for TDM-bound requests
    if not _is_tdm_request(provider, base_url):
        return None

    # Parse config
    tdm_config = {}
    if config and isinstance(config, dict):
        tdm_config = config.get("tdm", {}).get("complexity", {})
        if not isinstance(tdm_config, dict):
            tdm_config = {}

    # Check enabled flag (default: true)
    if tdm_config.get("enabled", True) is False:
        return None

    # Priority 1: config override
    override = tdm_config.get("override")
    if override and override in VALID_VALUES:
        return override

    # Priority 2: env var override
    env_override = os.environ.get("HERMES_TDM_COMPLEXITY", "").strip().lower()
    if env_override in VALID_VALUES:
        return env_override

    # Priority 3+4: classify by context
    if _is_cron_context(platform):
        value = tdm_config.get("cron", DEFAULT_COMPLEXITY_MAP["cron"])
    else:
        value = tdm_config.get("default", DEFAULT_COMPLEXITY_MAP["interactive"])

    # Contract: exactly low|moderate|high, lowercase — anything else means
    # "don't emit" so TDM falls back to its own computed score.
    value = str(value).strip().lower()
    return value if value in VALID_VALUES else None


def inject_complexity_header(
    api_kwargs: dict,
    *,
    provider: str,
    base_url: str,
    platform: str,
    config: Optional[dict] = None,
) -> dict:
    """Inject X-TDM-Complexity header into api_kwargs if appropriate.

    Mutates and returns api_kwargs. Adds the header to extra_headers
    (merging with any existing headers). Logs the declared value for
    joinability with TDM shadow logs.
    """
    complexity = classify_complexity(
        provider=provider,
        base_url=base_url,
        platform=platform,
        config=config,
    )
    if complexity is None:
        return api_kwargs

    # Merge into existing extra_headers (don't clobber other headers)
    existing_headers = dict(api_kwargs.get("extra_headers") or {})
    existing_headers[TDM_COMPLEXITY_HEADER] = complexity
    api_kwargs["extra_headers"] = existing_headers

    logger.info(
        "[tdm-complexity] declared=%s provider=%s platform=%s",
        complexity, provider, platform or "interactive",
    )

    return api_kwargs
