"""Unit normalisation via pint.

Converts quantities between units, handles volume↔mass via ingredient density,
and provides human-readable display strings.
"""
from __future__ import annotations

from typing import Optional

import pint

ureg = pint.UnitRegistry()
ureg.define("count = [] = each = piece = pcs")


def to_base(quantity: float, unit: str) -> pint.Quantity:
    """Convert (quantity, unit_string) to a pint Quantity."""
    try:
        return quantity * ureg(unit)
    except pint.errors.UndefinedUnitError:
        # Fall back to 'count' for unrecognised units
        return quantity * ureg.count


def convert(
    quantity: float,
    from_unit: str,
    to_unit: str,
    density_g_per_ml: Optional[float] = None,
) -> float:
    """Convert quantity from_unit → to_unit.

    If the conversion crosses the mass/volume boundary, density_g_per_ml is
    required. Raises ValueError if the conversion is impossible.
    """
    q = to_base(quantity, from_unit)
    target = ureg(to_unit)

    # Try direct dimensional conversion first
    try:
        return q.to(target).magnitude  # type: ignore[union-attr]
    except pint.errors.DimensionalityError:
        pass

    if density_g_per_ml is None:
        raise ValueError(
            f"Cannot convert {from_unit} → {to_unit} without ingredient density."
        )

    # Mass → volume or volume → mass via density
    q_in_g = q.to(ureg.gram) if q.dimensionality == ureg.gram.dimensionality else (
        q.to(ureg.mL) * (density_g_per_ml * ureg("g/mL"))
    )
    try:
        return q_in_g.to(target).magnitude  # type: ignore[union-attr]
    except pint.errors.DimensionalityError as exc:
        raise ValueError(f"Unsupported conversion: {from_unit} → {to_unit}") from exc


def display(quantity: float, unit: str) -> str:
    """Human-friendly quantity string, e.g. '1.5 kg', '3 count'."""
    q = to_base(quantity, unit)
    # Compact large quantities: g→kg, mL→L
    try:
        if q.dimensionality == ureg.gram.dimensionality and q.magnitude >= 1000:
            q = q.to(ureg.kilogram)
        elif q.dimensionality == ureg.mL.dimensionality and q.magnitude >= 1000:
            q = q.to(ureg.liter)
    except Exception:
        pass
    mag = q.magnitude
    if isinstance(mag, float) and mag == int(mag):
        mag = int(mag)
    unit_str = str(q.units)
    if unit_str == "count":
        return str(mag)
    return f"{mag} {unit_str}"
