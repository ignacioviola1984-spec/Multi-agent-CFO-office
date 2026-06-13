"""
embedder.py - Motor de embeddings, con backend intercambiable.

Primario: sentence-transformers (sobre PyTorch). Es el estandar de la
industria y lo que aparece en las busquedas laborales.
Fallback: model2vec (embeddings estaticos sobre numpy, sin torch), por si
la instalacion de PyTorch falla en este entorno.

El resto del sistema (search, rag) no sabe ni le importa cual se usa: solo
llama a embed(). Cambiar de backend (sentence-transformers, Voyage, OpenAI)
no toca el codigo de busqueda.
"""

_model = None
_backend = None


def _load():
    global _model, _backend
    if _model is not None:
        return
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        _backend = "sentence-transformers (PyTorch)"
    except Exception:
        from model2vec import StaticModel
        _model = StaticModel.from_pretrained("minishlab/potion-base-8M")
        _backend = "model2vec (fallback, sin torch)"


def embed(texts):
    import numpy as np
    _load()
    return np.asarray(_model.encode(list(texts)), dtype="float32")


def backend_name():
    _load()
    return _backend
