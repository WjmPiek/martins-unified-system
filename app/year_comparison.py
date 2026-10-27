"""Display-only comparison of like-for-like actuals; ranking stays unchanged."""
from decimal import Decimal


def yearly_indicator(current, previous, weights, metric_key='overall'):
    if current is None or previous is None:
        return {'yearly_direction': 'unavailable', 'yearly_label': 'No comparable data last year', 'yearly_change': Decimal('0')}
    if metric_key == 'overall':
        # Normalize each KPI before combining money and operational counts.
        difference = sum((weight * (Decimal(current.get(key, 0)) - Decimal(previous.get(key, 0)))
                          / max(abs(Decimal(previous.get(key, 0))), Decimal('1'))
                          for key, weight in weights.items()), Decimal('0'))
    else:
        difference = Decimal(current.get(metric_key, 0)) - Decimal(previous.get(metric_key, 0))
    direction = 'up' if difference > 0 else 'down' if difference < 0 else 'same'
    label = {'up': 'Higher than the same period last year',
             'down': 'Lower than the same period last year',
             'same': 'Unchanged from the same period last year'}[direction]
    return {'yearly_direction': direction, 'yearly_label': label, 'yearly_change': difference}
