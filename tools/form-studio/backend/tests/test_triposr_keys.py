"""TripoSR loads with transformers 4 or 5: its checkpoint's ViT keys are renamed to what the model expects."""
import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from formstudio.api.worker import _friendly  # noqa: E402
from formstudio.engines.base import EngineError  # noqa: E402
from formstudio.engines.local import _match_state_dict, _rename_vit  # noqa: E402


class Holder(torch.nn.Module):
    """Like TSR: the ViT sits at image_tokenizer.model, next to other parts."""

    def __init__(self):
        super().__init__()
        from transformers import ViTConfig, ViTModel

        cfg = ViTConfig(hidden_size=32, num_hidden_layers=2, num_attention_heads=2, intermediate_size=64,
                        image_size=32, patch_size=16)
        self.image_tokenizer = torch.nn.Module()
        self.image_tokenizer.model = ViTModel(cfg, add_pooling_layer=False)
        self.backbone = torch.nn.Linear(4, 4)


def _other_naming(sd, to_new):
    return {_rename_vit(k, to_new): v.clone() for k, v in sd.items()}


def test_checkpoint_with_the_other_naming_loads():
    m = Holder()
    sd = m.state_dict()
    keys = set(sd)
    new_style = any(".layers.0.attention.q_proj" in k for k in keys)
    # the checkpoint as the *other* transformers major version names it (TripoSR's is the old one)
    ckpt = _other_naming(sd, to_new=not new_style)
    assert set(ckpt) != keys  # the bug: a plain load_state_dict fails
    with pytest.raises(RuntimeError):
        Holder().load_state_dict(ckpt)
    fresh = Holder()
    fresh.load_state_dict(_match_state_dict(ckpt, keys))  # strict, and it loads
    for k, v in fresh.state_dict().items():
        assert torch.equal(v, sd[k])


def test_unrelated_mismatch_is_readable():
    m = Holder()
    ckpt = dict(m.state_dict())
    ckpt.pop("backbone.weight")
    with pytest.raises(EngineError) as e:
        _match_state_dict(ckpt, set(m.state_dict()))
    assert "don't match the installed AI library" in str(e.value) and len(str(e.value)) < 300


def test_huge_library_errors_are_short():
    keys = ", ".join(f'"image_tokenizer.model.layers.{i}.attention.q_proj.weight"' for i in range(200))
    msg = _friendly(RuntimeError(f"Error(s) in loading state_dict for TSR:\n\tMissing key(s) in state_dict: {keys}"))
    assert len(msg) < 400 and "From the picture" in msg
    assert len(_friendly(RuntimeError("x" * 5000))) <= 401
