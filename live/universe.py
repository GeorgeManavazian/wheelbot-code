"""Live trading universe: price-diverse, liquid, OPTIONABLE. Spans price tiers so
the bot is testable across $5k-$500k accounts (one put ties up strike*100
collateral, so small accounts can only trade cheap underlyings). Owner-editable.
Filtering to what an account can afford is a decision-layer concern (sub-project B).

Curated for LIQUID LISTED OPTIONS only: no mutual funds (zero options), no
delisted names, no dotted symbols (Schwab API can't route "BRK.B"). Tier bands
are approximate reference prices — variety is the point, not exact levels."""

# Low ($5-50): the inventory a ~$5k account can actually trade (one put ~ $500-5k).
_LOW = [
    "GDX", "GDXJ", "SLV", "XOP", "EWZ", "EEM", "FXI", "USO", "KRE", "XLF",
    "KVUE", "F", "SOFI", "NIO", "RIG", "SNAP", "T", "BAC", "WFC", "PFE",
    "INTC", "CSCO", "VALE", "NEM", "CCL", "MARA", "RIOT", "CLSK", "HOOD",
    "LYFT", "AAL", "UAL", "PBR", "KGC", "AGNC", "NOK", "SIRI", "CLF", "X",
    "UPST", "CHPT", "GPRO", "BITO", "KMI", "PLUG", "AG", "HL", "SCCO",
]
# Mid ($50-150).
_MID = [
    "GLD", "XBI", "XLE", "XLK", "SMH", "IWM", "DIA", "EFA", "TLT", "HYG",
    "LQD", "VWO", "VEA", "AMD", "BABA", "PYPL", "UBER", "DIS", "KO", "PEP",
    "CVX", "XOM", "WMT", "SBUX", "NKE", "C", "GM", "SNOW", "SHOP", "MRNA",
    "ROKU", "PLTR", "MU", "XYZ", "PINS", "DKNG", "CVS", "MO", "DAL", "OXY",
]
# High ($150-700+): only larger accounts reach these.
_HIGH = [
    "SPY", "QQQ", "VOO", "IVV", "IWF", "AAPL", "MSFT", "AMZN", "GOOGL",
    "META", "NVDA", "TSLA", "NFLX", "AVGO", "CRM", "ADBE", "COST", "HD",
    "UNH", "LLY", "V", "MA", "JPM", "GS", "CAT", "BA", "AMAT", "NOW",
    "PANW", "LRCX", "ISRG", "MELI", "MSTR", "TSM", "ORCL", "ACN", "QCOM",
    "TXN", "INTU", "SPGI",
]
UNIVERSE = _LOW + _MID + _HIGH
