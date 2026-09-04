"""The synthetic stream: schema, entities, scenarios, schedule, and the builder.

Every identifier is synthetic and drawn from reserved ranges; the label and
scenario columns are partitioned from the feature columns at import time
(`schema.py`). `build.py` writes the train/test streams and the manifest;
`ASSUMPTIONS.md` records what the generator assumes and what it measured.
"""
