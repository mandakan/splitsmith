# Vendored BEATs source

Files vendored from [microsoft/unilm/beats](https://github.com/microsoft/unilm/tree/master/beats)
@ master, MIT licensed (see `LICENSE.microsoft-unilm`). Used by
`splitsmith.ensemble.features.load_beats_runtime` and the issue #179
comparison experiment.

Local modifications:
- Sibling-module imports converted to relative (`from backbone import ...`
  -> `from .backbone import ...`) so the directory works as a Python
  package when surfaced on `sys.path`.

To use, point `SPLITSMITH_BEATS_CHECKPOINT` at a fine-tuned BEATs `.pt`
checkpoint (e.g. `BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2.pt` from
`huggingface.co/camenduru/beats`) and call
`load_beats_runtime()`. Only finetuned checkpoints work; pretraining
checkpoints have no predictor head.
