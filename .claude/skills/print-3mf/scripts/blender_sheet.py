"""Composite Blender RGBA renders onto a light studio background as a labelled 4-view sheet."""
import sys, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
d_in, out = sys.argv[1], sys.argv[2]
bg = np.array([246, 246, 244], float)
names = ['front', 'side', 'back', 'iso']; labels = ['Front View', 'Side View', 'Back View (Flat)', 'Isometric View']
ims = []
for n in names:
    im = np.asarray(Image.open(os.path.join(d_in, f'render_{n}.png')).convert('RGBA')).astype(float)
    a = im[..., 3] / 255
    solid = a > 0.995
    sh = np.clip((a - 0.055) / 0.945, 0, 1) * 0.55          # soften shadow, drop denoiser ghosts
    a = np.where(solid, a, sh)[..., None]
    ims.append(Image.fromarray((im[..., :3] * a + bg * (1 - a)).astype(np.uint8)))
W, Hh = ims[0].size
fp = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
font = ImageFont.truetype(fp, 30) if os.path.exists(fp) else ImageFont.load_default()
sheet = Image.new('RGB', (W * 4, Hh + 70), tuple(bg.astype(int))); d = ImageDraw.Draw(sheet)
for i, (im, lab) in enumerate(zip(ims, labels)):
    sheet.paste(im, (i * W, 0))
    tw = d.textlength(lab, font=font); x = i * W + (W - tw) / 2
    d.rounded_rectangle((x - 22, Hh + 8, x + tw + 22, Hh + 56), 22, fill=(228, 228, 226))
    d.text((x, Hh + 14), lab, fill=(40, 40, 40), font=font)
sheet.save(out)
sheet.resize((sheet.size[0] // 2, sheet.size[1] // 2)).save(out.replace('.png', '_small.png'))
