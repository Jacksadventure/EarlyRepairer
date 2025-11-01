"""
Minimal lstar package exports for the new architecture.
Only expose ObservationTable used by the L* learner in repairer_lstar_ec.py.
"""

from .observation_table import ObservationTable

__all__ = ["ObservationTable"]
