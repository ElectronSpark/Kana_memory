"""The content caches around a test that patches a synthetic course in.

drop_content_caches drops every cache derived from the course content (content_caches.clear_all)
before the test and again when it ends: nothing computed from the synthetic course outlives the
test, and nothing computed from the real course is used while the synthetic one is patched in.
A test that patches the course again in the middle calls content_caches.clear_all() itself.
Consumers import the fixture explicitly (support/README.txt).
"""
import pytest

from kanamemory import content_caches


@pytest.fixture
def drop_content_caches(request):
    content_caches.clear_all()
    request.addfinalizer(content_caches.clear_all)
