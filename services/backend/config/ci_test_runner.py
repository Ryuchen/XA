"""Tag live HTTP tests so CI can give their server threads separate connections."""
from unittest import TestLoader

from django.test import LiveServerTestCase
from django.test.runner import DiscoverRunner


class LiveServerTaggingLoader(TestLoader):
    def loadTestsFromTestCase(self, testCaseClass):
        if issubclass(testCaseClass, LiveServerTestCase):
            testCaseClass.tags = set(getattr(testCaseClass, 'tags', ())) | {'filedb'}
        return super().loadTestsFromTestCase(testCaseClass)


class SplitLiveServerRunner(DiscoverRunner):
    test_loader = LiveServerTaggingLoader()
