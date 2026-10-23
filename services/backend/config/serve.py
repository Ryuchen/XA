"""Daphne's normal CLI with access-log credentials removed before formatting."""
from daphne.cli import CommandLineInterface
from daphne.server import Server

from common.logging_utils import redact_sensitive_urls


class RedactedServer(Server):
    def log_action(self, protocol, action, details):
        if 'path' in details:
            details = {**details, 'path': redact_sensitive_urls(details['path'])}
        super().log_action(protocol, action, details)


class CommandLine(CommandLineInterface):
    server_class = RedactedServer


if __name__ == '__main__':
    CommandLine.entrypoint()
