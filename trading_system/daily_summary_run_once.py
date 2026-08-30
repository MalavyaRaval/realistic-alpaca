"""Single invocation that builds and prints the ABEV daily summary. Meant
to be fired once daily, shortly after market close, by an external
scheduler - see the Windows Scheduled Task "ABEV_DailySummary".
"""

from daily_summary import build_daily_summary, format_summary

SYMBOL = "ABEV"


def main():
    summary = build_daily_summary(SYMBOL)
    print(format_summary(summary))


if __name__ == "__main__":
    main()
