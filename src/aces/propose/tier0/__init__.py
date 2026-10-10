"""Tier 0 training pipeline: dataset -> model -> evaluation (D26, D29, D31).

Clean-room and dependency-free (numpy/scipy only — no external code or
weights, so the licence gate in aces.propose.contract is satisfied by
construction, ``licence="internal"``).

The reference model is deliberately the FLOOR, not the goal: a
PCA-conditional Gaussian sampler that any future latent-diffusion proposer
must beat on the same evaluation harness (evaluate.py). The harness — not
the model — is the point of building this before the corpus is large
enough to train a real model: campaign data drops into a ready machine.
"""
