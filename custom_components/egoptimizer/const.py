"""Constants for the EGOptimizer integration."""

from __future__ import annotations

DOMAIN = "egoptimizer"
PLATFORMS = ["sensor", "number", "select", "button"]

# config entry keys
CONF_BRAIN_URL = "brain_url"
CONF_CAPACITY_KWH = "capacity_kwh"
CONF_SOC_ENTITY = "soc_entity"
CONF_LOAD_ENTITY = "load_entity"
CONF_SOLCAST_ENTITY = "solcast_entity"            # Prognose heute
CONF_SOLCAST_TOMORROW_ENTITY = "solcast_tomorrow_entity"  # Prognose morgen
CONF_HARD_MIN_ENTITY = "hard_min_entity"
CONF_SCAN_MINUTES = "scan_minutes"

# options / runtime controls (also surfaced as HA entities)
CONF_TARGET_MORNING_SOC = "target_morning_soc_pct"
CONF_MODE = "mode"

CONF_RETENTION_DAYS = "retention_days"
CONF_AGGRESSIVENESS = "exploration_aggressiveness"
CONF_LOAD_AVG_MINUTES = "load_average_minutes"

DEFAULT_SCAN_MINUTES = 15
DEFAULT_TARGET_MORNING_SOC = 50.0
DEFAULT_MODE = "explore"
DEFAULT_RETENTION_DAYS = 1095          # ~3 years; 0 = keep everything
DEFAULT_AGGRESSIVENESS = 0.15
DEFAULT_LOAD_AVG_MINUTES = 15          # rolling-average window for the load sensor
MODES = ["explore", "locked"]

# How often (seconds) we sample the raw load into the rolling-average buffer.
# Decoupled from the recompute interval so smoothing works regardless of it.
LOAD_SAMPLE_SECONDS = 30

# Solcast detailedHourly attribute (varies by integration version).
SOLCAST_ATTR = "detailedHourly"
