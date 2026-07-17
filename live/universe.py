"""Live trading universe: price-diverse, liquid, optionable. Spans price tiers so
the bot is testable across $5k-$500k accounts (one put ties up strike*100
collateral, so small accounts can only trade cheap underlyings). Owner-editable.
Filtering to what an account can afford is a decision-layer concern (sub-project B)."""

# Low ($5-50): the inventory a ~$5k account can actually trade.
_LOW = [
    "GDX", "SLV", "XOP", "EWZ", "GDXJ", "XLF", "KRE", "EEM", "FXI", "USO",
    "SOFI", "F", "PLTR", "NIO", "RIG", "SNAP", "T", "BAC", "WFC", "PFE",
    "INTC", "CSCO", "KVUE", "VALE", "GOLD", "CCL", "MARA", "RIOT", "CHPT",
    "LYFT", "HOOD", "AAL", "UAL", "PBR", "KGC", "AGNC", "NOK", "SIRI",
    "GEVO", "BITF", "CLSK", "MSTR", "UPST", "SCCO", "HUT", "GLIBA", "X", "MT",
    "RS", "CLF", "ARCH", "AVLR", "HYCH", "GPRO", "ARCC", "MAIN", "ORC", "OXLC",
]
# Mid ($50-150).
_MID = [
    "GLD", "XBI", "XLE", "XLK", "SMH", "IWM", "DIA", "EFA", "TLT", "HYG",
    "AMD", "BABA", "PYPL", "UBER", "DIS", "KO", "PEP", "CVX", "XOM", "WMT",
    "SBUX", "NKE", "MU", "C", "GM", "COIN", "SNOW", "SHOP", "MRNA", "ROKU",
    "SEMI", "EWG", "EWJ", "EWU", "FXE", "EWI", "IVV", "VTV", "VOE", "VBR",
    "AGG", "LQD", "VCIT", "SCHZ", "VGIT", "PFF", "VCSH", "ANGL", "IBND", "MBB",
]
# High ($150-700+): only larger accounts reach these.
_HIGH = [
    "SPY", "QQQ", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA",
    "NFLX", "AVGO", "CRM", "ADBE", "COST", "HD", "UNH", "LLY", "V", "MA",
    "JPM", "GS", "CAT", "BA", "AMAT", "NOW", "PANW", "LRCX", "ISRG", "MELI",
    "BRK.B", "VOO", "VEA", "VWO", "VBTLX", "FSKAX", "FTIHX",
    "VFIAX", "VFITX", "FXAIX", "FZROX", "SCHB", "SWTSX", "SPLG", "SCHX", "SUSA", "SCHG",
]
UNIVERSE = _LOW + _MID + _HIGH
