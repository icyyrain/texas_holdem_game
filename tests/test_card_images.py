from pathlib import Path

from holdem.client.card_images import card_back_path, card_image_name, card_image_path, resource_root
from holdem.core.cards import Card, Deck


def test_card_image_name_uses_card_code():
    assert card_image_name(Card("A", "C")) == "AC.png"
    assert card_image_name(Card("T", "H")) == "TH.png"
    assert card_image_name(Card("K", "S")) == "KS.png"


def test_card_image_path_uses_resource_root():
    root = Path("assets")

    assert card_image_path(Card("Q", "D"), root=root) == root / "cards" / "QD.png"


def test_resource_root_points_at_project_assets_in_source_tree():
    assert resource_root().name == "assets"


def test_every_card_has_a_png_in_the_source_tree():
    for card in Deck.standard().cards:
        assert card_image_path(card).is_file(), card
    assert card_back_path().is_file()
