# SPDX-License-Identifier: Apache-2.0

"""ONNX-backed embedder (e5-small via onnxruntime + tokenizers).

This module is import-light by default — :mod:`onnxruntime` and
:mod:`tokenizers` are imported lazily on first :meth:`OnnxE5Embedder.embed`
call so a user without the optional deps still gets a clean error when
they ask for the ONNX backend, rather than a startup ImportError.

Model artefacts are NOT bundled in the wheel (would balloon the package
to ~100 MiB). Users install them once into
``~/.cache/steward/models/<model-id>/`` — the path defaults to
:func:`default_model_dir` and can be overridden via constructor args.
The expected directory layout matches a HuggingFace ``snapshot_download``
of the model's ONNX export:

::

    <model-dir>/
    ├── model.onnx
    ├── tokenizer.json
    └── config.json   (optional)

A typical bootstrap step (operator-driven, not automated):

::

    huggingface-cli download intfloat/multilingual-e5-small \\
      --local-dir ~/.cache/steward/models/multilingual-e5-small

The default model name is ``multilingual-e5-small`` (384-dim, matches
the schema). Switching to a different 384-dim model only requires
pointing ``--model-dir`` somewhere else.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from steward.core.embed import (
    EMBEDDING_DIMENSION,
    EmbedderInfo,
    Embedding,
    EmbedRequest,
)


def default_model_dir(model_name: str = "multilingual-e5-small") -> Path:
    """Return the default location for a bundled-or-downloaded ONNX model.

    ``platformdirs.user_cache_dir("steward")`` resolves to
    ``~/.cache/steward`` on Linux/macOS. The path is computed; the
    directory may not exist yet.
    """
    base = Path(platformdirs.user_cache_dir("steward"))
    return base / "models" / model_name


class OnnxModelNotFoundError(FileNotFoundError):
    """The configured ONNX model directory is missing required files.

    Raised when ``OnnxE5Embedder`` is asked to embed but the model has
    not been downloaded. The CLI converts this into a friendly hint to
    run ``huggingface-cli download …``.
    """


@dataclass
class _LoadedSession:
    """Bundle the lazy-loaded ONNX session + tokenizer."""

    session: object
    tokenizer: object
    model_version: str


class OnnxE5Embedder:
    """E5-family ONNX embedder.

    Lazy-loaded: the heavy imports + session construction happen on the
    first :meth:`embed` call, not on ``__init__``. This keeps Steward
    startup fast and lets users without ``onnxruntime`` installed import
    this module without crashing.
    """

    def __init__(
        self,
        *,
        model_name: str = "multilingual-e5-small",
        model_dir: Path | None = None,
    ) -> None:
        self._model_name = model_name
        self._model_dir = model_dir or default_model_dir(model_name)
        self._loaded: _LoadedSession | None = None
        self._info = EmbedderInfo(
            model_name=model_name,
            model_version="pending",
            dimension=EMBEDDING_DIMENSION,
        )

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    @property
    def info(self) -> EmbedderInfo:
        # The "version" stays "pending" until first load — that's
        # intentional, so describing an unloaded embedder is cheap.
        return self._info

    def _resolve_paths(self) -> tuple[Path, Path]:
        model_path = self._model_dir / "model.onnx"
        tok_path = self._model_dir / "tokenizer.json"
        if not model_path.exists() or not tok_path.exists():
            raise OnnxModelNotFoundError(
                f"ONNX model not found under {self._model_dir} "
                f"(expected model.onnx + tokenizer.json). "
                f"Install with: huggingface-cli download "
                f"intfloat/{self._model_name} --local-dir {self._model_dir}"
            )
        return model_path, tok_path

    def _load(self) -> _LoadedSession:
        if self._loaded is not None:
            return self._loaded
        model_path, tok_path = self._resolve_paths()
        # Lazy imports — these are heavy modules.
        import onnxruntime  # type: ignore[import-not-found]
        from tokenizers import Tokenizer  # type: ignore[import-not-found]

        session = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        tokenizer = Tokenizer.from_file(str(tok_path))
        # Best-effort version: the size of the model file is a stable
        # proxy. Real production would read a config.json.
        version = f"{self._model_name}-{os.path.getsize(model_path)}"
        self._info = EmbedderInfo(
            model_name=self._model_name,
            model_version=version,
            dimension=EMBEDDING_DIMENSION,
        )
        self._loaded = _LoadedSession(
            session=session, tokenizer=tokenizer, model_version=version
        )
        return self._loaded

    @staticmethod
    def _mean_pool_normalize(
        token_embeddings: object, attention_mask: object
    ) -> tuple[float, ...]:  # pragma: no cover - requires onnxruntime tensors
        """Mean-pool the token embeddings under the attention mask and
        L2-normalise the result.

        Implemented inline because we don't ship numpy in v0.2 dependencies;
        instead we operate on the onnxruntime output directly. The runtime
        returns numpy arrays; we depend on that ABI here. Tests stub this
        module entirely so they don't exercise it.
        """
        import numpy as np  # type: ignore[import-not-found]

        emb = np.asarray(token_embeddings)
        mask = np.asarray(attention_mask).astype("float32")
        # (batch, tokens, dim) * (batch, tokens, 1)
        mask = np.expand_dims(mask, -1)
        summed = (emb * mask).sum(axis=1)
        denom = np.clip(mask.sum(axis=1), 1e-9, None)
        pooled = summed / denom
        # L2-normalise.
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        normed = pooled / np.clip(norm, 1e-12, None)
        return tuple(float(x) for x in normed[0])

    def embed(self, request: EmbedRequest) -> Embedding:  # pragma: no cover - requires real model
        loaded = self._load()
        # tokenizers returns an Encoding with .ids and .attention_mask
        enc = loaded.tokenizer.encode(  # type: ignore[attr-defined]
            f"passage: {request.text}"
        )
        ids = [enc.ids]
        mask = [enc.attention_mask]
        # Many e5 ONNX exports expect input_ids + attention_mask + token_type_ids;
        # introspect the session's inputs to be flexible.
        inputs = {
            i.name: (
                [ids] if i.name == "input_ids" else
                [mask] if i.name == "attention_mask" else
                [[0] * len(ids[0])]
            )
            for i in loaded.session.get_inputs()  # type: ignore[attr-defined]
        }
        outputs = loaded.session.run(None, inputs)  # type: ignore[attr-defined]
        vec = self._mean_pool_normalize(outputs[0], mask)
        return Embedding(
            permanode_id=request.permanode_id,
            info=self._info,
            vector=vec,
        )

    def embed_batch(
        self, requests: list[EmbedRequest]
    ) -> list[Embedding]:
        # Simple loop — v0.2 baseline. A future change could batch
        # token sequences with padding for real throughput.
        return [self.embed(r) for r in requests]


__all__ = [
    "OnnxE5Embedder",
    "OnnxModelNotFoundError",
    "default_model_dir",
]
