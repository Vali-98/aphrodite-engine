import contextlib
from typing import Type
import torch
import torch.nn as nn
from transformers import PretrainedConfig

from aphrodite.common.config import ModelConfig
from aphrodite.modeling.models import (LlamaForCausalLM, GPTJForCausalLM,
                                       GPTNeoXForCausalLM, MistralForCausalLM,
                                       YiForCausalLM)
from aphrodite.modeling.hf_downloader import (initialize_dummy_weights,
                                              get_quant_config)
from aphrodite.modeling.layers.quantized_linear.utils import quant_post_init

_MODEL_REGISTRY = {
    "LlamaForCausalLM": LlamaForCausalLM,
    "LLaMAForCausalLM": LlamaForCausalLM,
    "GPTJForCausalLM": GPTJForCausalLM,
    "GPTNeoXForCausalLM": GPTNeoXForCausalLM,
    "MistralForCausalLM": MistralForCausalLM,
    "YiForCausalLM": YiForCausalLM,
}

_MODEL_CLASSES_SUPPORT_QUANTIZATION = {
    "awq": [LlamaForCausalLM, MistralForCausalLM, YiForCausalLM],
    "gptq": [
        LlamaForCausalLM, GPTJForCausalLM, GPTNeoXForCausalLM,
        MistralForCausalLM, YiForCausalLM
    ],
}


@contextlib.contextmanager
def _set_default_torch_dtype(dtype: torch.dtype):
    """Sets the default torch dtype to the given dtype."""
    old_dtype = torch.get_default_dtype()
    torch.set_default_dtype(dtype)
    yield
    torch.set_default_dtype(old_dtype)


def _get_model_architecture(config: PretrainedConfig) -> Type[nn.Module]:
    architectures = getattr(config, "architectures", [])
    for arch in architectures:
        if arch in _MODEL_REGISTRY:
            return _MODEL_REGISTRY[arch]
    raise ValueError(
        f"Model architectures {architectures} are not supported for now. "
        f"Supported architectures: {list(_MODEL_REGISTRY.keys())}")


def get_model(model_config: ModelConfig, max_tokens: int) -> nn.Module:
    model_class = _get_model_architecture(model_config.hf_config)

    # Get the quantization config.
    quant_config = None
    if model_config.quantization is not None:
        if model_class not in _MODEL_CLASSES_SUPPORT_QUANTIZATION[
                model_config.quantization]:
            raise ValueError(
                f"Quantization is not supported for {model_class}.")
        quant_config = get_quant_config(model_config.quantization,
                                        model_config.model,
                                        model_config.hf_config,
                                        model_config.download_dir)
        capability = torch.cuda.get_device_capability()
        capability = capability[0] * 10 + capability[1]
        if capability < quant_config.get_min_capability():
            raise ValueError(
                f"The quantization method {model_config.quantization} is not "
                "supported for the current GPU. "
                f"Minimum capability: {quant_config.get_min_capability()}. "
                f"Current capability: {capability}.")
        supported_dtypes = quant_config.get_supported_act_dtypes()
        if model_config.dtype not in supported_dtypes:
            raise ValueError(
                f"{model_config.dtype} is not supported for quantization "
                f"method {model_config.quantization}. Supported dtypes: "
                f"{supported_dtypes}")

    with _set_default_torch_dtype(model_config.dtype):
        # Create a model instance.
        # The weights will be initialized as empty tensors.
        if model_config.quantization is not None and (
                model_class in _MODEL_CLASSES_SUPPORT_QUANTIZATION[
                    model_config.quantization]):
            model = model_class(model_config.hf_config, quant_config)
        else:
            model = model_class(model_config.hf_config)
        if model_config.load_format == "dummy":
            model = model.cuda()
            # NOTE(woosuk): For accurate performance evaluation, we assign
            # random values to the weights.
            initialize_dummy_weights(model)
        else:
            # Load the weights from the cached or downloaded files.
            model.load_weights(model_config.model, model_config.download_dir,
                               model_config.load_format, model_config.revision)
            model = model.cuda()
        if model_config.quantization is not None:
            quant_post_init(model, max_tokens)
    return model.eval()
