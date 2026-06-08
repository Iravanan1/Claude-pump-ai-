#!/usr/bin/env python3
"""
Nozzle Flow Meter Rollover and Mechanical Reset Calculation Handler.
Defines calculation logic for normal subtraction, mechanical rollovers,
and manual meter replacement overrides.
"""

def calculate_net_nozzle_volume(opening, closing, max_digits=999999, meter_replaced=False, replacement_offset_liters=0.0):
    """
    Calculates the net nozzle volume, accounting for mechanical rollovers and replacements.
    
    Args:
        opening (float): Opening totalizer meter reading.
        closing (float): Closing totalizer meter reading.
        max_digits (int): Rollover ceiling threshold (default: 999999).
        meter_replaced (bool): Flag indicating if the meter was replaced during the shift.
        replacement_offset_liters (float): Volume from the replaced meter before swap.
        
    Returns:
        float: Net liters dispensed, rounded to 2 decimal places.
    """
    opening = float(opening or 0.0)
    closing = float(closing or 0.0)
    replacement_offset_liters = float(replacement_offset_liters or 0.0)
    
    if meter_replaced:
        # Replaced meter: Flow on new meter (closing - opening) + offset volume from old meter
        net_liters = (closing - opening) + replacement_offset_liters
    elif closing >= opening:
        # Standard flow
        net_liters = closing - opening
    else:
        # Rollover detected
        net_liters = (float(max_digits) - opening) + closing
        
    return round(net_liters, 2)
