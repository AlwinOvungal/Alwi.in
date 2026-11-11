"""Hunyuan3D's DINOv2 image encoder under transformers 5 (renamed weights)."""
import sys
import types

import pytest
import torch

from formstudio.engines import local

transformers = pytest.importorskip("transformers")
from transformers import Dinov2Config, Dinov2Model  # noqa: E402


def _dino() -> Dinov2Model:
    torch.manual_seed(0)
    cfg = Dinov2Config(hidden_size=64, num_hidden_layers=2, num_attention_heads=4, use_swiglu_ffn=True,
                       image_size=28, patch_size=14, layerscale_value=1.0)
    return Dinov2Model(cfg).eval()


def _old_names(sd: dict, prefix: str = "") -> dict:
    """The same weights as an old (transformers 4) checkpoint names them, like Hunyuan3D's model file."""
    out = {}
    back = [(new, old) for old, new in local.DINOV2_RENAMES]
    for k, v in sd.items():
        if ".mlp.up_proj." in k:
            continue
        if ".mlp.gate_proj." in k:
            up = sd[k.replace(".mlp.gate_proj.", ".mlp.up_proj.")]
            out[prefix + k.replace(".mlp.gate_proj.", ".mlp.weights_in.")] = torch.cat([v, up], dim=0)
            continue
        for new, old in back:
            k = k.replace(new, old)
        out[prefix + k] = v
    return out


def test_old_dinov2_weights_convert_to_the_same_model():
    src = _dino()
    old = _old_names(src.state_dict())
    assert any(".attention.attention.query." in k for k in old) and any(".mlp.weights_in." in k for k in old)
    fresh = _dino()
    for p in fresh.parameters():  # make sure every value really comes from the converted file
        torch.nn.init.zeros_(p)
    fresh.load_state_dict(local.convert_dinov2_keys(old), strict=True)
    x = torch.randn(1, 3, 28, 28)
    with torch.no_grad():
        assert torch.allclose(src(x).last_hidden_state, fresh(x).last_hidden_state, atol=1e-6)


def test_new_names_pass_through_unchanged():
    sd = _dino().state_dict()
    assert local.convert_dinov2_keys(sd) is sd


def test_hunyuan_encoder_loads_its_old_file(monkeypatch):
    class SingleImageEncoder(torch.nn.Module):  # shaped like hy3dgen's: main_image_encoder.model = Dinov2Model
        def __init__(self):
            super().__init__()
            self.main_image_encoder = torch.nn.Module()
            self.main_image_encoder.model = _dino()

    cond = types.ModuleType("hy3dgen.shapegen.models.conditioner")
    cond.SingleImageEncoder = SingleImageEncoder
    for name in ("hy3dgen", "hy3dgen.shapegen", "hy3dgen.shapegen.models"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "hy3dgen.shapegen.models.conditioner", cond)
    sys.modules["hy3dgen.shapegen.models"].conditioner = cond

    old = _old_names(SingleImageEncoder().state_dict())
    with pytest.raises(RuntimeError, match="Missing key"):  # what you saw on the Mac
        SingleImageEncoder().load_state_dict(old)
    local._hy3dgen_compat()
    local._hy3dgen_compat()  # twice is fine
    SingleImageEncoder().load_state_dict(old)  # strict, now loads


def test_unloadable_hunyuan_gives_a_plain_message():
    from formstudio.engines.base import EngineError

    def make():
        raise RuntimeError('Error(s) in loading state_dict for SingleImageEncoder: Missing key(s) in state_dict: "x"')

    local.unload_all()
    with pytest.raises(EngineError) as e:
        local._hunyuan_load(make, "hunyuan-mv", "cpu")
    assert "couldn't load its model" in str(e.value) and "SingleImageEncoder" in e.value.detail
    assert any("TRELLIS" in s for s in e.value.steps)
