"""Bid Alert tender screening pipeline.

Stages: ingest -> normalise -> dedup -> eligibility -> score -> store -> report -> alert.
See docs/SYSTEM_DESIGN.md for the architecture and docs/LEARNING_GUIDE.md for a tour.
"""

__version__ = "0.2.0"
