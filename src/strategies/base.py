"""Strategy plugin contract. A strategy is a pure function of PAST closes:
window (wide closes, ending at decision day) -> target weights or None (hold).
The engine fills at next open — strategies never touch prices or shares.
"""


class Strategy:
    name = "base"
    DEFAULTS: dict = {}
    description = ""  # plain language: What it does / Why it should work / When it fails
    display_name = ""  # human name shown in the dashboard, e.g. "Trend Following"

    def __init__(self, **params):
        unknown = set(params) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {unknown}")
        self.params = {**self.DEFAULTS, **params}

    def target_weights(self, window):
        raise NotImplementedError

    @staticmethod
    def is_month_start(window) -> bool:
        """True when the window's last bar is the first trading day of a month."""
        if len(window) < 2:
            return False
        return window.index[-1].month != window.index[-2].month

    def label(self) -> str:
        p = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}({p})"
