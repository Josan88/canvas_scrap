"""Speed tweaks for the opendataloader_pdf hybrid server (docling) formula model.

Applied inside the hybrid server process, before it builds its converter.
Settings are passed from the parent process through environment variables.
"""

import os

FORMULA_BATCH_ENV = "CANVASYNC_FORMULA_BATCH"
FORMULA_MAX_TOKENS_ENV = "CANVASYNC_FORMULA_MAX_TOKENS"


def apply_hybrid_server_tuning() -> None:
    """Patch docling's formula model using the CANVASYNC_FORMULA_* env vars."""
    from docling.models.stages.code_formula import code_formula_vlm_model as cfm

    # docling sends 5 formulas per generate() call; larger batches are nearly
    # free on GPU because token-by-token decoding is memory-bound.
    batch = os.environ.get(FORMULA_BATCH_ENV)
    if batch:
        cfm.CodeFormulaVlmModel.elements_batch_size = int(batch)

    # docling hardcodes max_new_tokens=2048. A formula the model cannot read
    # (e.g. handwriting) can loop until that limit, and the whole batch waits on it.
    max_tokens = os.environ.get(FORMULA_MAX_TOKENS_ENV)
    if max_tokens:
        cap = int(max_tokens)
        original_input = cfm.VlmEngineInput

        def capped_input(**kwargs):
            kwargs["max_new_tokens"] = min(kwargs.get("max_new_tokens", cap), cap)
            return original_input(**kwargs)

        cfm.VlmEngineInput = capped_input
