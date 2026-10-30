"""Kill switch from PowerShell (folder C:\\Users\\USER\\Desktop\\Kunal\\kunal-fyers-terminal).

    .venv\\Scripts\\python.exe kill_switch.py           shows whether it is on and until when
    .venv\\Scripts\\python.exe kill_switch.py --off     ends it early (asks you to type a phrase)

To switch it ON use the red KILL SWITCH button in the terminal (it also cancels orders and closes positions).
"""

import argparse

from terminal.killswitch import RELEASE_PHRASE, KillSwitch


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--off", action="store_true")
    a = ap.parse_args()
    k = KillSwitch()
    if not a.off:
        print(k.message() or "Kill switch is OFF: trading allowed.")
        return
    if not k.active():
        print("Kill switch is already OFF.")
        return
    print(f"{k.message()}\nType exactly:  {RELEASE_PHRASE}")
    k.release(input("> "))
    print("Kill switch OFF. Restart the terminal (close the black window, double-click start_terminal.bat).")


if __name__ == "__main__":
    main()
