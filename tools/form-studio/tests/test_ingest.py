from PIL import Image

from formstudio.ingest import crop_to_square, remove_background_threshold


def test_threshold_matte_separates_object(sample_photo):
    cut = remove_background_threshold(Image.open(sample_photo).convert("RGBA"))
    alpha = cut.getchannel("A")
    assert alpha.getpixel((10, 10)) == 0          # background
    assert alpha.getpixel((350, 300)) == 255      # object body


def test_crop_to_square_is_square_and_sized(sample_photo):
    cut = remove_background_threshold(Image.open(sample_photo).convert("RGBA"))
    out = crop_to_square(cut, size=256)
    assert out.size == (256, 256)
    assert out.mode == "RGB"
    # corners of the padded canvas are white
    assert out.getpixel((0, 0)) == (255, 255, 255)
