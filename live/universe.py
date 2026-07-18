"""Live trading universe: price-diverse, liquid, OPTIONABLE. Spans price tiers so
the bot is testable across $5k-$500k accounts (one put ties up strike*100
collateral, so small accounts can only trade cheap underlyings). Owner-editable.

Sourced from the S&P 500 + Nasdaq 100 + liquid ETFs + a low-price cohort for
small-account testing. Curated for LIQUID LISTED OPTIONS: no mutual funds, no
dotted symbols (Schwab can't route "BRK.B"). A delisted/renamed ticker degrades
gracefully -- LiveMarket skips it and logs it (see market_live.skipped) -- so a
stale entry costs nothing but a logged skip. Re-vet quarterly for delistings."""

# Liquid index / sector / commodity / bond ETFs.
_ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "VOO", "IVV", "VTI", "VEA", "VWO", "EFA",
    "EEM", "FXI", "EWZ", "GLD", "SLV", "GDX", "GDXJ", "USO", "XOP", "XLE",
    "XLF", "XLK", "XLV", "XLI", "XLP", "XLU", "XLB", "XLY", "XLRE", "XLC",
    "SMH", "SOXL", "XBI", "KRE", "ARKK", "TLT", "HYG", "LQD", "AGG", "IEF",
    "VNQ", "IBIT", "KWEB",
]

# Low-price cohort ($5-50) -- the inventory a ~$5k account can trade.
_CHEAP = [
    "SOFI", "NIO", "RIG", "SNAP", "NOK", "SIRI", "PLUG", "AG", "HL", "MARA",
    "RIOT", "CLSK", "HOOD", "LYFT", "PBR", "KGC", "AGNC", "VALE", "BITO", "AA",
    "F", "T", "BAC", "INTC", "CHPT", "GPRO", "UPST", "CCL", "CVS", "PARA",
    "KVUE",
]

# S&P 500 / large-cap, A-M.
_SP_AM = [
    "MMM", "AOS", "ABT", "ABBV", "ACN", "ADBE", "AMD", "AES", "AFL", "A",
    "APD", "ABNB", "AKAM", "ALB", "ARE", "ALGN", "ALLE", "LNT", "ALL", "GOOGL",
    "GOOG", "MO", "AMZN", "AMCR", "AEE", "AEP", "AXP", "AIG", "AMT", "AWK",
    "AMP", "AME", "AMGN", "APH", "ADI", "AON", "APA", "APO", "AAPL", "AMAT",
    "APP", "APTV", "ACGL", "ADM", "ARES", "ANET", "AJG", "AIZ", "T", "ATO",
    "ADSK", "ADP", "AZO", "AVB", "AVY", "AXON", "BKR", "BALL", "BAC", "BAX",
    "BDX", "BBY", "TECH", "BIIB", "BLK", "BX", "XYZ", "BNY", "BA", "BKNG",
    "BSX", "BMY", "AVGO", "BR", "BRO", "BLDR", "BG", "BXP", "CHRW", "CDNS",
    "CPT", "COF", "CAH", "CCL", "CARR", "CVNA", "CASY", "CAT", "CBOE", "CBRE",
    "CDW", "COR", "CNC", "CNP", "CF", "CRL", "SCHW", "CHTR", "CVX", "CMG",
    "CB", "CHD", "CIEN", "CI", "CINF", "CTAS", "CSCO", "C", "CFG", "CLX",
    "CME", "CMS", "KO", "CTSH", "COHR", "COIN", "CL", "CMCSA", "FIX", "COP",
    "ED", "STZ", "CEG", "COO", "CPRT", "GLW", "CPAY", "CTVA", "CSGP", "COST",
    "CRH", "CRWD", "CCI", "CSX", "CMI", "CVS", "DHR", "DRI", "DDOG", "DVA",
    "DECK", "DE", "DELL", "DAL", "DVN", "DXCM", "FANG", "DLR", "DG", "DLTR",
    "D", "DPZ", "DASH", "DOV", "DOW", "DHI", "DTE", "DUK", "DD", "ETN",
    "EBAY", "ECL", "EIX", "EW", "EA", "ELV", "EME", "EMR", "ETR", "EOG",
    "EQT", "EFX", "EQIX", "EQR", "ESS", "EL", "EG", "EVRG", "ES", "EXC",
    "EXPE", "EXPD", "EXR", "XOM", "FFIV", "FDS", "FICO", "FAST", "FRT", "FDX",
    "FIS", "FITB", "FSLR", "FE", "FI", "FLEX", "F", "FTNT", "FTV", "FOXA",
    "FOX", "BEN", "FCX", "GRMN", "IT", "GE", "GEHC", "GEV", "GEN", "GNRC",
    "GD", "GIS", "GM", "GPC", "GILD", "GPN", "GL", "GDDY", "GS", "HAL",
    "HIG", "HAS", "HCA", "DOC", "HSIC", "HSY", "HPE", "HLT", "HD", "HON",
    "HRL", "HST", "HWM", "HPQ", "HUBB", "HUM", "HBAN", "HII", "IBM", "IEX",
    "IDXX", "ITW", "INCY", "IR", "PODD", "INTC", "IBKR", "ICE", "IFF", "IP",
    "INTU", "ISRG", "IVZ", "INVH", "IQV", "IRM", "JBHT", "JBL", "JKHY", "J",
    "JNJ", "JCI", "JPM", "KVUE", "KDP", "KEY", "KEYS", "KMB", "KIM", "KMI",
    "KKR", "KLAC", "KHC", "KR", "LHX", "LH", "LRCX", "LVS", "LDOS", "LEN",
    "LII", "LLY", "LIN", "LYV", "LMT", "L", "LOW", "LULU", "LITE", "LYB",
    "MTB", "MPC", "MAR", "MLM", "MRVL", "MAS", "MA", "MKC", "MCD", "MCK",
    "MDT", "MRK", "META", "MET", "MTD", "MGM", "MCHP", "MU", "MSFT", "MAA",
    "MRNA", "TAP", "MDLZ", "MPWR", "MNST", "MCO",
]

# S&P 500 / large-cap, N-Z.
_SP_NZ = [
    "NDAQ", "NTAP", "NFLX", "NEM", "NWSA", "NWS", "NEE", "NKE", "NI", "NDSN",
    "NOC", "NCLH", "NRG", "NUE", "NVDA", "NVR", "NXPI", "ORLY", "OXY", "ODFL",
    "OMC", "ON", "OKE", "ORCL", "OTIS", "PCAR", "PKG", "PLTR", "PANW", "PARA",
    "PH", "PAYX", "PAYC", "PYPL", "PNR", "PEP", "PFE", "PCG", "PM", "PSX",
    "PNW", "PNC", "POOL", "PPG", "PPL", "PFG", "PG", "PGR", "PLD", "PRU",
    "PEG", "PTC", "PSA", "PHM", "QCOM", "PWR", "DGX", "RL", "RJF", "RTX",
    "O", "REG", "REGN", "RF", "RSG", "RMD", "RVTY", "ROK", "ROL", "ROP",
    "ROST", "RCL", "SPGI", "CRM", "SBAC", "SLB", "STX", "SRE", "NOW", "SHW",
    "SPG", "SWKS", "SJM", "SW", "SNA", "SOLV", "SO", "LUV", "SWK", "SBUX",
    "STT", "STLD", "STE", "SYK", "SMCI", "SYF", "SNPS", "SYY", "TMUS", "TROW",
    "TTWO", "TPR", "TRGP", "TGT", "TEL", "TDY", "TER", "TSLA", "TXN", "TXT",
    "TMO", "TJX", "TSCO", "TT", "TDG", "TRV", "TRMB", "TFC", "TYL", "TSN",
    "USB", "UBER", "UDR", "ULTA", "UNP", "UAL", "UPS", "URI", "UNH", "UHS",
    "VLO", "VTR", "VLTO", "VRSN", "VRSK", "VZ", "VRTX", "VTRS", "VICI", "V",
    "VST", "VMC", "WAB", "WBA", "WMT", "DIS", "WBD", "WM", "WAT", "WEC",
    "WFC", "WELL", "WST", "WDC", "WY", "WMB", "WTW", "WDAY", "WYNN", "XEL",
    "XYL", "YUM", "ZBRA", "ZBH", "ZTS",
]

# First-seen dedup across the groups; order is stable (ETFs, then cheap, then S&P).
_ALL = _ETFS + _CHEAP + _SP_AM + _SP_NZ
UNIVERSE = list(dict.fromkeys(_ALL))
