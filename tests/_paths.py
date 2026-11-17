"""The tests' paths: the project, this folder, the sample data and the work folder,
temporary and outside the project (AIROS_TEST_WORK or /tmp/airos-tui-tests)."""
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "python-airos")
WORK = os.environ.get("AIROS_TEST_WORK") or os.path.join(tempfile.gettempdir(),
                                                         "airos-tui-tests")
os.makedirs(WORK, exist_ok=True)
