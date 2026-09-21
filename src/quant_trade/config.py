"""Central application and strategy configuration.

Tune strategy thresholds here. Secrets (database password, QQ bot tokens)
still come from environment variables via ``DatabaseSettings.from_env`` and
the QQ client helpers.
"""

# --- RSI trend-following (日线) ---
RSI_PERIOD = 14
MA_FAST = 20
MA_SLOW = 30
ATR_PERIOD = 14
RSI_ZONE_LOW = 42.0
RSI_ZONE_HIGH = 58.0
LONG_TRIGGER_RSI_LOW = 50.0
LONG_TRIGGER_RSI_HIGH = 58.0
SHORT_TRIGGER_RSI_LOW = 42.0
SHORT_TRIGGER_RSI_HIGH = 50.0
RECENT_RSI_DAYS = 5
MA_FAST_SLOPE_DAYS = 10
# Average daily MA return threshold as percent (0.3 means 0.3%/day).
MA_FAST_MIN_DAILY_RETURN_PCT = 0.3
MA_FAST_MIN_DAILY_RETURN = MA_FAST_MIN_DAILY_RETURN_PCT / 100.0
# When more than this many symbols match, raise the MA threshold.
MA_FAST_OVERFLOW_MATCH_LIMIT = 100
MA_FAST_OVERFLOW_MIN_DAILY_RETURN_PCT = 0.6
MA_FAST_OVERFLOW_MIN_DAILY_RETURN = MA_FAST_OVERFLOW_MIN_DAILY_RETURN_PCT / 100.0

# --- W/M pattern ---
WM_ATR_PERIOD = 14
WM_PIVOT_LEFT = 3
WM_PIVOT_RIGHT = 3
WM_MIN_PATTERN_DISTANCE = 5
WM_MAX_PATTERN_DISTANCE = 30
WM_MAX_PEAK_DIFFERENCE_ATR = 1.0
WM_MIN_MIDDLE_RETRACEMENT_ATR = 1.0
WM_BREAK_BUFFER_ATR = 0.1

# --- Scanner / charts ---
SCAN_LOOKBACK_BARS = 240
CHART_WINDOW_BARS = 100
SIGNAL_SHEET_COLUMNS = 4
# One QQ image is a SIGNAL_SHEET_COLUMNS × rows grid; 4×4 uses 16 charts.
CHARTS_PER_MESSAGE = SIGNAL_SHEET_COLUMNS * 4
