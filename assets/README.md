# Assets

## Card artwork

`poker-super2-box-top-qr/` holds the SVG card faces (`AS.svg`, `TH.svg`, ...) and backs (`1B.svg`, `2B.svg`), generated with Adrian Kennard's (RevK) playing card generator at <https://www.me.uk/cards/>. The generator page states the designs are released under the **CC0 Public Domain** licence; no attribution is required, but credit is appreciated.

`cards/` (96×134, board cards) and `cards_small/` (60×84, hole cards at the seats) hold the PNG versions used by the tkinter client; `back.png` comes from `1B.svg`. Regenerate them with:

```powershell
conda run -n holdem-game python scripts\prepare_card_images.py
conda run -n holdem-game python scripts\prepare_card_images.py --output-dir assets\cards_small --width 60 --height 84
```

`table.png` is a screenshot of the client for the README.

Both folders were copied from the `blackjack_game` project.
