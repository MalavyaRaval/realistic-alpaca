"""Deliberately resume monitoring after investigating a halt.

Usage: python clear_halt.py SYMBOL

Nothing in monitor.py ever calls this automatically - a halt means
something unexpected happened and stays in place until a person decides
it's actually been resolved.
"""

import sys

import monitor_state


def main():
    if len(sys.argv) != 2:
        print("Usage: python clear_halt.py SYMBOL")
        sys.exit(1)

    symbol = sys.argv[1]
    state = monitor_state.load_state()
    sym_state = monitor_state.get_symbol_state(state, symbol)

    if not sym_state["halted"]:
        print(f"{symbol} is not halted - nothing to clear.")
        return

    print(f"{symbol} was halted: {sym_state['halt_reason']}")
    monitor_state.clear_halt(symbol)
    print(f"Halt cleared for {symbol}. Monitoring will resume on the next cycle.")


if __name__ == "__main__":
    main()
