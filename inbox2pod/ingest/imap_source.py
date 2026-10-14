"""
IMAP ingest: the default front door.

Points at any existing mailbox (Gmail, Fastmail, ...) and pulls unread mail
from one folder. No DNS, no open ports, works behind CGNAT.
"""

from imap_tools import AND, MailBox

from inbox2pod.config import Config
from inbox2pod.ingest.base import IncomingEmail, parse_email


def email_from_imap(message) -> IncomingEmail:
    """Convert an imap-tools MailMessage into the pipeline's IncomingEmail."""
    return parse_email(message.obj.as_bytes())


class ImapSource:
    """Fetches new mail from an IMAP mailbox and applies the after action."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        folder: str | None = None,
        after_action: str | None = None,
        max_emails: int | None = None,
    ):
        self.host = host or Config.IMAP_HOST
        self.port = port or Config.IMAP_PORT
        self.username = username or Config.IMAP_USER
        self.password = password or Config.IMAP_PASSWORD
        self.folder = folder or Config.IMAP_FOLDER
        self.after_action = (after_action or Config.IMAP_AFTER_ACTION).strip().lower()
        self.max_emails = max_emails if max_emails is not None else Config.IMAP_MAX_EMAILS_PER_POLL

    def fetch(self) -> list[IncomingEmail]:
        """Fetch unread mail, apply the after action, return what was read."""
        emails: list[IncomingEmail] = []
        with MailBox(self.host, self.port).login(self.username, self.password, self.folder) as mailbox:
            messages = list(mailbox.fetch(AND(seen=False), mark_seen=False, limit=self.max_emails or None))

            for message in messages:
                try:
                    emails.append(email_from_imap(message))
                except Exception as e:
                    print(f"Skipping IMAP message uid={getattr(message, 'uid', '?')}: {e}")

            self._apply_after_action(mailbox, [message.uid for message in messages])

        return emails

    def _apply_after_action(self, mailbox, uids: list[str]) -> None:
        """Run IMAP_AFTER_ACTION on everything we just read."""
        if not uids or self.after_action == "none":
            return

        try:
            if self.after_action == "mark_seen":
                mailbox.flag(uids, "\\Seen", True)
            elif self.after_action == "delete":
                mailbox.delete(uids)
                mailbox.expunge()
            elif self.after_action.startswith("move:"):
                destination = self.after_action.split(":", 1)[1].strip()
                if not destination:
                    raise ValueError("move: needs a folder name")
                if not mailbox.folder.exists(destination):
                    mailbox.folder.create(destination)
                mailbox.move(uids, destination)
            else:
                raise ValueError(f"unknown IMAP_AFTER_ACTION {self.after_action!r}")
        except Exception as e:
            print(f"IMAP after action {self.after_action!r} failed for {len(uids)} message(s): {e}")
