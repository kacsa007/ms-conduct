"""Evidence checks for the Allianz Suisse conduct dossier.

The package has no third-party dependencies so that the collector, the
dashboard and offline validation can all share the same logic:

``evidence.text``        text normalisation, tokenisation and decoding
``evidence.matching``    anchors and relevance ranking for record excerpts
``evidence.signals``     wrongdoing signal detection with polarity guards
``evidence.schema``      dossier and snapshot integrity checks
``evidence.verification``cross-checks findings against collected snapshots
``evidence.io``          tolerant readers and atomic writers
"""

__version__ = "2.0.0"
