"""
L* algorithm main loop and convenience helpers.

Extracted from notebooks/2024-01-04-lstar-learning-regular-languages.py
and consolidated into a reusable module.
"""

from typing import Dict, List, Tuple
from .observation_table import ObservationTable
from .teacher import Teacher, Oracle


def l_star(T: ObservationTable, teacher: Oracle) -> Tuple[Dict[str, List[List[str]]], str]:
    """
    Run Angluin's L* algorithm using an observation table and a teacher/oracle.

    Returns:
        (grammar, start_symbol) as a CFG-style dictionary accepted by the utilities.
    """
    T.init_table(teacher)

    while True:
        while True:
            is_closed, unknown_P = T.closed()
            is_consistent, _, unknown_AS = T.consistent()
            if is_closed and is_consistent:
                break
            if not is_closed:
                T.add_prefix(unknown_P, teacher)
            if not is_consistent:
                T.add_suffix(unknown_AS, teacher)

        grammar, start = T.grammar()
        eq, counterX = teacher.is_equivalent(grammar, start)
        if eq:
            return grammar, start
        for i, _ in enumerate(counterX):
            T.add_prefix(counterX[0 : i + 1], teacher)


def learn_from_regex(
    regex: str,
    alphabet: List[str],
    delta: float = 0.1,
    epsilon: float = 0.1,
) -> Tuple[Dict[str, List[List[str]]], str]:
    """
    Convenience wrapper to learn a grammar for the given regular expression
    using the provided alphabet and PAC parameters.
    """
    t = Teacher(regex, delta=delta, epsilon=epsilon)
    T = ObservationTable(alphabet)
    return l_star(T, t)
