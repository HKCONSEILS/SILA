"""Tests unitaires pour le timing optimizer.

Voir MASTERPLAN.md §2.1 P2 — cascade de duree.
"""

from src.core.models import TimingFitStatus
from src.core.timing import classify_timing_fit, compute_stretch_ratio


def test_timing_fit_ok():
    """Segment dans la tolerance => fit_ok."""
    assert classify_timing_fit(5000, 5000) == TimingFitStatus.FIT_OK
    assert classify_timing_fit(5500, 5000) == TimingFitStatus.FIT_OK  # +10% < 15%


def test_timing_rewrite_needed():
    """Segment depassant la tolerance mais stretchable => rewrite_needed."""
    assert classify_timing_fit(6000, 5000) == TimingFitStatus.REWRITE_NEEDED  # ratio 1.2


def test_timing_review_required():
    """Segment depassant le max stretch => review_required."""
    assert classify_timing_fit(7000, 5000) == TimingFitStatus.REVIEW_REQUIRED  # ratio 1.4


def test_timing_zero_budget():
    assert classify_timing_fit(5000, 0) == TimingFitStatus.REVIEW_REQUIRED


def test_stretch_ratio_no_stretch():
    assert compute_stretch_ratio(4000, 5000) == 1.0


def test_stretch_ratio_needed():
    ratio = compute_stretch_ratio(6000, 5000)
    assert abs(ratio - 1.2) < 0.01
