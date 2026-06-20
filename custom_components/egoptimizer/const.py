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
CONF_BASE_LOAD_WINDOW_MINUTES = "base_load_window_minutes"
CONF_BASE_LOAD_PERCENTILE = "base_load_percentile"

DEFAULT_SCAN_MINUTES = 15
DEFAULT_TARGET_MORNING_SOC = 50.0
DEFAULT_MODE = "explore"
DEFAULT_RETENTION_DAYS = 1095          # ~3 years; 0 = keep everything
DEFAULT_AGGRESSIVENESS = 0.15
DEFAULT_LOAD_AVG_MINUTES = 15          # rolling-average window for "load now"
# Overnight base load: a low percentile of the load over a long window. This is
# the SUSTAINED baseline (fridge/standby) -- a realistic stand-in for the night
# draw, instead of the volatile instantaneous load that wrongly drained the
# simulated battery overnight.
DEFAULT_BASE_LOAD_WINDOW_MINUTES = 180
DEFAULT_BASE_LOAD_PERCENTILE = 25      # 0..100; lower = more feeding, higher = safer

# Overnight base load is best taken from RECORDER HISTORY of the load sensor:
# the chosen percentile of its hourly mean during the night window, over the
# last N days. Restart-proof and reflects the real night draw (no warm-up).
CONF_NIGHT_LOAD_OVERRIDE_KW = "night_load_override_kw"   # 0 = auto (use history)
DEFAULT_NIGHT_LOAD_OVERRIDE_KW = 0.0
NIGHT_START_HOUR = 0                   # local hour the "deep night" window starts
NIGHT_END_HOUR = 6                     # ...and ends (exclusive)
NIGHT_HISTORY_DAYS = 7                 # how far back to read for the estimate
NIGHT_LOAD_REFRESH_MIN = 60            # recompute the history estimate at most hourly
MODES = ["explore", "locked"]

# How often (seconds) we sample the raw load into the rolling buffer.
# Decoupled from the recompute interval so smoothing works regardless of it.
LOAD_SAMPLE_SECONDS = 30

# Solcast detailedHourly attribute (varies by integration version).
SOLCAST_ATTR = "detailedHourly"
