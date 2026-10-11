import gc

import pytest


@pytest.fixture(autouse=True)
def release_loop_objects():
    yield
    # Release cyclic test doubles/estimators before another module's timing checks.
    gc.collect()
