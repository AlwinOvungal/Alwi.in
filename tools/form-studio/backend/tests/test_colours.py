from PIL import Image

from formstudio.core import colours


def test_picks_the_object_colour_not_the_white_background():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    img.paste(Image.new("RGB", (40, 40), (40, 80, 200)), (30, 30))
    out = colours.suggest([img])
    assert out["body"]["name"] in ("Cobalt", "Sky Blue") and out["body"]["hex"].startswith("#")
    assert out["base"]["hex"] != out["body"]["hex"]


def test_names_and_fallback():
    assert colours.name_for((94, 107, 46)) == "Olive"
    assert colours.suggest([Image.new("RGB", (20, 20), (255, 255, 255))])["body"]["name"] == "Olive"
