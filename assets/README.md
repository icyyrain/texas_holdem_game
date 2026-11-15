# Assets

## Card artwork

`poker-super2-box-top-qr/` holds the SVG card faces (`AS.svg`, `TH.svg`, ...) and backs (`1B.svg`, `2B.svg`), generated with Adrian Kennard's (RevK) playing card generator at <https://www.me.uk/cards/>. The generator page states the designs are released under the **CC0 Public Domain** licence; no attribution is required, but credit is appreciated.

`cards/` holds the PNG versions used by the tkinter client (96×134, `back.png` from `1B.svg`). Regenerate them with:

```powershell
conda run -n holdem-game python scripts\prepare_card_images.py
```

Both folders were copied from the `blackjack_game` project.
