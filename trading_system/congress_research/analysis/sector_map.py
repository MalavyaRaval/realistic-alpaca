"""Manually curated GICS-style sector classification for the tickers most
frequently traded in the congress dataset (2021+ window).

This is NOT sourced from a live classifier or vendor feed - it's a
best-effort manual mapping based on well-known public information about
each company's primary business, used only because Alpaca's asset API
does not expose sector/industry data and no free sector-classification
API was set up for this analysis. A ticker not confidently known is left
out entirely (falls back to "Unclassified") rather than guessed - "do not
invent missing information" applies to sector labels as much as to the
disclosure data itself. Anything that isn't a common stock (Treasury
bills, ETFs/funds) gets FUND_OR_TREASURY, not a sector, and is excluded
from sector-level performance breakdowns.

Sector benchmark ETFs are the standard SPDR Select Sector funds - real,
liquid, widely-used proxies, not something invented for this analysis.
"""

FUND_OR_TREASURY = "Fund/ETF/Treasury (excluded from sector breakdown)"

SECTOR_BENCHMARK_ETF = {
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

TICKER_SECTOR = {
    # Information Technology
    "MSFT": "Information Technology", "AAPL": "Information Technology",
    "NVDA": "Information Technology", "AVGO": "Information Technology",
    "CRM": "Information Technology", "ADBE": "Information Technology",
    "INTC": "Information Technology", "AMD": "Information Technology",
    "TXN": "Information Technology", "CSCO": "Information Technology",
    "IBM": "Information Technology", "ORCL": "Information Technology",
    "LRCX": "Information Technology", "INTU": "Information Technology",
    "QCOM": "Information Technology", "ACN": "Information Technology",
    "TSM": "Information Technology", "AMAT": "Information Technology",
    "NOW": "Information Technology", "ASML": "Information Technology",
    "PANW": "Information Technology", "FIS": "Information Technology",
    "ANET": "Information Technology", "ADI": "Information Technology",
    "CRWD": "Information Technology", "MU": "Information Technology",
    "WDAY": "Information Technology", "ADP": "Information Technology",
    "KLAC": "Information Technology", "NXPI": "Information Technology",
    "CDNS": "Information Technology", "GDDY": "Information Technology",
    "DELL": "Information Technology", "SNPS": "Information Technology",
    "MRVL": "Information Technology", "SHOP": "Information Technology",
    "APP": "Information Technology", "PTC": "Information Technology",
    "TEL": "Information Technology", "FTNT": "Information Technology",
    "CTSH": "Information Technology", "ADSK": "Information Technology",
    "ENTG": "Information Technology", "IT": "Information Technology",
    "FLEX": "Information Technology", "GPN": "Information Technology",
    "PLTR": "Information Technology", "COIN": "Information Technology",
    "TTD": "Information Technology", "VRSK": "Information Technology",
    "HDB": "Information Technology",
    # Health Care
    "JNJ": "Health Care", "UNH": "Health Care", "LLY": "Health Care",
    "PFE": "Health Care", "ABBV": "Health Care", "MRK": "Health Care",
    "TMO": "Health Care", "ABT": "Health Care", "AMGN": "Health Care",
    "MDT": "Health Care", "BSX": "Health Care", "CVS": "Health Care",
    "SYK": "Health Care", "MCK": "Health Care", "ZTS": "Health Care",
    "ISRG": "Health Care", "BMY": "Health Care", "DHR": "Health Care",
    "CI": "Health Care", "REGN": "Health Care", "CNC": "Health Care",
    "ELV": "Health Care", "GILD": "Health Care", "HCA": "Health Care",
    "IDXX": "Health Care", "ILMN": "Health Care", "EW": "Health Care",
    "NVO": "Health Care", "NVS": "Health Care", "KVUE": "Health Care",
    "BDX": "Health Care", "BIIB": "Health Care", "LH": "Health Care",
    "DGX": "Health Care", "BAX": "Health Care", "HUM": "Health Care",
    "WAT": "Health Care", "ALGN": "Health Care", "MRNA": "Health Care",
    "GSK": "Health Care", "AZN": "Health Care", "RHHBY": "Health Care",
    "PAYX": "Industrials",  # payroll services, not health - kept separate below
    # Financials
    "JPM": "Financials", "V": "Financials", "MA": "Financials",
    "GS": "Financials", "WFC": "Financials", "BAC": "Financials",
    "BLK": "Financials", "MS": "Financials", "AXP": "Financials",
    "SPGI": "Financials", "SCHW": "Financials", "C": "Financials",
    "CB": "Financials", "BRO": "Financials", "PNC": "Financials",
    "USB": "Financials", "BK": "Financials", "COF": "Financials",
    "CME": "Financials", "ICE": "Financials", "AON": "Financials",
    "MMC": "Financials", "AJG": "Financials", "WRB": "Financials",
    "FITB": "Financials", "KEY": "Financials", "LPLA": "Financials",
    "NDAQ": "Financials", "KKR": "Financials", "BX": "Financials",
    "HOOD": "Financials", "SQ": "Financials", "FI": "Financials",
    "BRK.B": "Financials",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "LOW": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "BKNG": "Consumer Discretionary", "TJX": "Consumer Discretionary",
    "CMG": "Consumer Discretionary", "TGT": "Consumer Discretionary",
    "AZO": "Consumer Discretionary", "ORLY": "Consumer Discretionary",
    "LULU": "Consumer Discretionary", "WYNN": "Consumer Discretionary",
    "F": "Consumer Discretionary", "GM": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary", "ABNB": "Consumer Discretionary",
    "MAR": "Consumer Discretionary", "HLT": "Consumer Discretionary",
    "DHI": "Consumer Discretionary", "CCL": "Consumer Discretionary",
    "AAL": "Consumer Discretionary", "LUV": "Consumer Discretionary",
    "URI": "Consumer Discretionary", "MELI": "Consumer Discretionary",
    "DASH": "Consumer Discretionary", "UBER": "Consumer Discretionary",
    "BABA": "Consumer Discretionary", "EA": "Consumer Discretionary",
    "MTCH": "Consumer Discretionary", "GNRC": "Industrials",
    "W": "Consumer Discretionary",
    # Consumer Staples
    "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "WMT": "Consumer Staples", "COST": "Consumer Staples", "MDLZ": "Consumer Staples",
    "MO": "Consumer Staples", "PM": "Consumer Staples", "CL": "Consumer Staples",
    "KMB": "Consumer Staples", "EL": "Consumer Staples", "HSY": "Consumer Staples",
    "GIS": "Consumer Staples", "CLX": "Consumer Staples", "SYY": "Consumer Staples",
    "KR": "Consumer Staples", "DG": "Consumer Staples", "TSCO": "Consumer Staples",
    "STZ": "Consumer Staples", "CHD": "Consumer Staples", "MNST": "Consumer Staples",
    "DEO": "Consumer Staples", "UL": "Consumer Staples", "BTI": "Consumer Staples",
    "BJ": "Consumer Staples", "CAG": "Consumer Staples", "NSRGY": "Consumer Staples",
    # Energy
    "CVX": "Energy", "XOM": "Energy", "COP": "Energy", "DVN": "Energy",
    "OXY": "Energy", "FCX": "Materials", "KMI": "Energy", "WMB": "Energy",
    "OKE": "Energy", "SLB": "Energy", "HAL": "Energy", "VLO": "Energy",
    "ET": "Energy", "PXD": "Energy", "AM": "Energy", "ENLC": "Energy",
    "NGL": "Energy", "PAA": "Energy", "USAC": "Energy", "ARLP": "Energy",
    "BP": "Energy", "SHLX": "Energy", "PBFX": "Energy", "FLNG": "Energy",
    # Industrials
    "UPS": "Industrials", "BA": "Industrials", "DE": "Industrials",
    "LMT": "Industrials", "FDX": "Industrials", "CAT": "Industrials",
    "ETN": "Industrials", "HON": "Industrials", "RTX": "Industrials",
    "UNP": "Industrials", "MMM": "Industrials", "GD": "Industrials",
    "NOC": "Industrials", "EMR": "Industrials", "GE": "Industrials",
    "PH": "Industrials", "TT": "Industrials", "NSC": "Industrials",
    "PWR": "Industrials", "LHX": "Industrials", "ROK": "Industrials",
    "ITW": "Industrials", "CSX": "Industrials", "WAB": "Industrials",
    "CARR": "Industrials", "TDG": "Industrials", "HUBB": "Industrials",
    "AA": "Materials", "X": "Materials", "CLF": "Materials",
    "HOG": "Consumer Discretionary", "CTAS": "Industrials",
    # Materials
    "LIN": "Materials", "SHW": "Materials", "APD": "Materials",
    "PPG": "Materials", "ECL": "Materials", "GOLD": "Materials",
    "RIO": "Materials", "BHP": "Materials", "IFF": "Materials",
    # Real Estate
    "PLD": "Real Estate", "AMT": "Real Estate", "CCI": "Real Estate",
    "SPG": "Real Estate", "CSGP": "Real Estate",
    # Utilities
    "NEE": "Utilities", "D": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    # Communication Services
    "META": "Communication Services", "FB": "Communication Services",
    "GOOGL": "Communication Services", "GOOG": "Communication Services",
    "NFLX": "Communication Services", "DIS": "Communication Services",
    "CMCSA": "Communication Services", "T": "Communication Services",
    "VZ": "Communication Services", "TMUS": "Communication Services",
    "WBD": "Communication Services", "SPOT": "Communication Services",
    "SNAP": "Communication Services", "TWTR": "Communication Services",
    "RBLX": "Communication Services", "SONY": "Communication Services",
    "Z": "Communication Services", "ATVI": "Communication Services",
    "FWONK": "Communication Services",
    # A second pass over names not caught above but confidently known:
    "PYPL": "Financials", "PGR": "Financials", "MKL": "Financials",
    "HTGC": "Financials", "FISV": "Financials",  # old ticker for Fiserv/FI
    "SAP": "Information Technology", "FSLR": "Information Technology",
    "SCI": "Consumer Discretionary", "ROP": "Industrials", "VRT": "Industrials",
    "GEV": "Industrials", "PKG": "Materials",
}

# Not operating companies - excluded from sector-level breakdowns rather
# than force-fit into a GICS sector.
NON_SECTOR_TICKERS = {
    "US-TBILL", "SPY", "IVV", "TNA", "BITB",
}


def get_sector(ticker: str) -> str:
    if not ticker:
        return "Unclassified"
    ticker = ticker.upper()
    if ticker in NON_SECTOR_TICKERS:
        return FUND_OR_TREASURY
    return TICKER_SECTOR.get(ticker, "Unclassified")


def get_sector_benchmark(sector: str):
    return SECTOR_BENCHMARK_ETF.get(sector)
