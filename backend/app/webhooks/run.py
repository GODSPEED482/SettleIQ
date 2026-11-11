"""The webhook processor process (task 4.4, spec 6.3, 10.4, AC-PP-05).

`python -m app.webhooks.run` drains the inbox apart from receipt: the public route only
verifies and stores, so a slow run or a failing event never delays PayPal's 2xx. It needs no
port; it shares the API's database role and claims rows with FOR UPDATE SKIP LOCKED, so more
than one may run.
"""

import logging
import threading

from app.webhooks.processor import claim_and_process_pending_webhooks

logger = logging.getLogger("settleiq.webhooks.run")

POLL_SECONDS = 2
BATCH = 50


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    while not stop.is_set():
        try:
            done = claim_and_process_pending_webhooks(limit=BATCH)
        except Exception:
            # The database may be restarting; rows stay claimable, so wait and try again.
            logger.exception("Webhook processing pass failed")
            done = []
        if len(done) < BATCH:
            stop.wait(POLL_SECONDS)


if __name__ == "__main__":
    main()
