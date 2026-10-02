"""Location inference (F4, ADR-0005, ADR-0015).

Eleven candidate producers feed one weighted consensus, three suppression rules and a
dual strict/advisory output. Every candidate -- winner, loser or suppressed -- is kept,
because that table *is* the derivation trail (F4.AC6, F4.AC11).

Layout:

* ``types``     -- the value objects every stage passes around
* ``config``    -- versioned weights and thresholds (F4.AC14)
* ``consensus`` -- the pure decision: candidates in, located visit out
* ``sources``   -- the producers, one module each
* ``engine``    -- runs the producers with timeouts and persists the result
"""
